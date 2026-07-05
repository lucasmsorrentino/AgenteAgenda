"""Obsidian vault client.

Replaces Anytype as the local knowledge store. Works directly with the
Obsidian vault filesystem (markdown files with YAML frontmatter), so no
running Obsidian REST API is required.

Folder layout inside the vault:
    tarefas/        -> task objects
    compromissos/   -> calendar appointments
    notas/          -> quick notes
    recaps/         -> daily recaps

Each file has YAML frontmatter with structured properties (done, due_date,
area, prioridade, tags, calendar_event_id, etc.) and markdown body.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

from config.settings import OBSIDIAN_AGENDA_SUBDIR, OBSIDIAN_VAULT_PATH, TIMEZONE

VAULT_PATH = Path(OBSIDIAN_VAULT_PATH)

# All agenda notes live under this subfolder (its own vault "section"), so they
# never scatter across the vault root: <vault>/agenda/{tarefas,compromissos,...}.
AGENDA_SUBDIR = OBSIDIAN_AGENDA_SUBDIR

TYPE_FOLDERS = {
    "tarefa": "tarefas",
    "task": "tarefas",
    "compromisso": "compromissos",
    "compromisso_custom": "compromissos",
    "page": "recaps",
    "nota_rapida": "notas",
    "note": "notas",
}

# Maps Anytype-style property keys to frontmatter keys.
_PROPERTY_KEY_MAP = {
    "done": "done",
    "due_date": "due_date",
    "tag": "tags",
    "tags": "tags",
    "start": "start",
    "end": "end",
    "location": "location",
    "calendar_event_id": "calendar_event_id",
    "recurring": "recurring",
    "area": "area",
    "prioridade": "prioridade",
    "classified_at": "classified_at",
}


def _slugify(name: str) -> str:
    """Create a filesystem-safe slug from a title."""
    text = unicodedata.normalize("NFKD", name)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[-\s]+", "-", text)
    return text[:80] or "sem-titulo"


def _unique_path(folder: Path, slug: str) -> Path:
    """Return a unique file path inside folder, appending a counter if needed."""
    folder.mkdir(parents=True, exist_ok=True)
    candidate = folder / f"{slug}.md"
    if not candidate.exists():
        return candidate
    counter = 1
    while True:
        candidate = folder / f"{slug}-{counter}.md"
        if not candidate.exists():
            return candidate
        counter += 1


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter and body from markdown text.

    Returns a simple flat dict. Supports strings, booleans, and lists of
    strings. Everything else is kept as a string.
    """
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, flags=re.DOTALL)
    if not match:
        return {}, text

    fm_text, body = match.group(1), match.group(2)
    data: dict = {}
    current_key: str | None = None

    for raw_line in fm_text.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        # List item under current key
        if line.startswith("  - ") and current_key is not None:
            value = line[4:].strip()
            if current_key not in data:
                data[current_key] = []
            if isinstance(data[current_key], list):
                data[current_key].append(_parse_scalar(value))
            continue

        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        current_key = key

        if value == "":
            data[key] = []
            continue
        data[key] = _parse_scalar(value)

    return data, body


def _parse_scalar(value: str) -> str | bool | None:
    """Parse a simple YAML scalar."""
    value = value.strip()
    if value == "true":
        return True
    if value == "false":
        return False
    if value == "null" or value == "~":
        return None
    # Strip surrounding quotes
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def _serialize_scalar(value) -> str:
    """Serialize a simple frontmatter value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ""
    text = str(value)
    # Quote strings that look like scalars or contain special characters
    if text in ("true", "false", "null", "~", "") or any(c in text for c in ":#[]{}|>&*!?,"):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


def _dump_frontmatter(data: dict) -> str:
    """Serialize a flat dict as YAML frontmatter."""
    lines = ["---"]
    for key, value in data.items():
        if isinstance(value, (list, tuple)):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {_serialize_scalar(item)}")
        else:
            lines.append(f"{key}: {_serialize_scalar(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _read_note(path: Path) -> tuple[dict, str]:
    """Read frontmatter and body from a note file."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        text = ""
    return _parse_frontmatter(text)


def _write_note(path: Path, frontmatter: dict, body: str) -> bool:
    """Write frontmatter + body to a note file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = _dump_frontmatter(frontmatter) + (body or "").strip() + "\n"
        path.write_text(text, encoding="utf-8")
        return True
    except Exception as e:
        logger.error("Failed to write note {}: {}", path, e)
        return False


def _property_to_frontmatter(prop: dict) -> tuple[str, object] | None:
    """Convert an Anytype-style property dict to a frontmatter key/value pair."""
    key = prop.get("key", "")
    fm_key = _PROPERTY_KEY_MAP.get(key)
    if not fm_key:
        return None

    if "checkbox" in prop:
        return fm_key, bool(prop["checkbox"])
    if "date" in prop:
        return fm_key, prop["date"]
    if "text" in prop:
        return fm_key, prop["text"]
    if "multi_select" in prop:
        items = prop["multi_select"]
        if items and isinstance(items[0], dict):
            return fm_key, [i.get("name", "") for i in items]
        return fm_key, list(items)
    if "select" in prop:
        item = prop["select"]
        if isinstance(item, dict):
            return fm_key, item.get("name", "")
        return fm_key, item
    return None


class ObsidianClient:
    """Filesystem-based client for an Obsidian vault."""

    def __init__(self, vault_path: Path | str | None = None):
        self.vault_path = Path(vault_path or VAULT_PATH)
        # Base folder for all agenda notes (the vault "section").
        self.base = self.vault_path / AGENDA_SUBDIR
        self.tz = ZoneInfo(TIMEZONE)

    # --- Connection ---

    def verify_connection(self) -> bool:
        """Check if the vault directory exists and is writable."""
        try:
            if not self.vault_path.exists():
                logger.warning("Obsidian vault not found at {}", self.vault_path)
                return False
            self.base.mkdir(parents=True, exist_ok=True)
            logger.info("Obsidian vault ready: {} (agenda: {})", self.vault_path, self.base)
            return True
        except Exception as e:
            logger.warning("Obsidian connection failed: {}", e)
            return False

    # --- Objects CRUD ---

    def create_object(
        self,
        type_key: str,
        name: str,
        body: str = "",
        icon: str = "",
        description: str = "",
        properties: list | None = None,
    ) -> str | None:
        """Create a markdown note in the vault. Returns relative object_id."""
        folder_name = TYPE_FOLDERS.get(type_key, type_key)
        folder = self.base / folder_name
        path = _unique_path(folder, _slugify(name))

        frontmatter: dict = {
            "id": path.stem,
            "type": type_key,
            "title": name,
            "created_at": datetime.now(self.tz).isoformat(),
        }
        if icon:
            frontmatter["icon"] = icon
        if description:
            frontmatter["description"] = description

        if properties:
            for prop in properties:
                pair = _property_to_frontmatter(prop)
                if pair:
                    frontmatter[pair[0]] = pair[1]

        content = body or description or ""
        if _write_note(path, frontmatter, content):
            rel_id = str(path.relative_to(self.vault_path))
            logger.info("Created Obsidian note: {} ({}) [type={}]", name, rel_id, type_key)
            return rel_id
        return None

    def update_object_properties(self, object_id: str, properties: list) -> bool:
        """Update properties in frontmatter."""
        path = self.vault_path / object_id
        if not path.exists():
            return False

        fm, body = _read_note(path)
        updated = False
        for prop in properties:
            pair = _property_to_frontmatter(prop)
            if pair:
                fm[pair[0]] = pair[1]
                updated = True

        if not updated:
            return True
        return _write_note(path, fm, body)

    def get_object(self, object_id: str) -> dict | None:
        """Get a note's frontmatter + body."""
        path = self.vault_path / object_id
        if not path.exists():
            return None
        fm, body = _read_note(path)
        fm["_body"] = body
        fm["_path"] = str(path)
        fm["id"] = object_id
        return fm

    def update_object(self, object_id: str, **kwargs) -> bool:
        """Update name, body, description, or properties of a note."""
        path = self.vault_path / object_id
        if not path.exists():
            return False

        fm, body = _read_note(path)
        if "name" in kwargs:
            fm["title"] = kwargs["name"]
        if "description" in kwargs:
            fm["description"] = kwargs["description"]
        if "body" in kwargs:
            body = kwargs["body"]
        if "properties" in kwargs:
            for prop in kwargs["properties"]:
                pair = _property_to_frontmatter(prop)
                if pair:
                    fm[pair[0]] = pair[1]

        fm["updated_at"] = datetime.now(self.tz).isoformat()
        ok = _write_note(path, fm, body)
        if ok:
            logger.info("Updated Obsidian note: {}", object_id)
        return ok

    def delete_object(self, object_id: str) -> bool:
        """Delete a note file."""
        path = self.vault_path / object_id
        if not path.exists():
            return False
        try:
            path.unlink()
            logger.info("Deleted Obsidian note: {}", object_id)
            return True
        except Exception as e:
            logger.error("Failed to delete Obsidian note {}: {}", object_id, e)
            return False

    # --- Search / Query ---

    def search_objects(self, query: str = "", types: list[str] | None = None, limit: int = 50) -> list[dict]:
        """Search notes by title/body and optional type filter."""
        query_lower = (query or "").lower()
        results: list[dict] = []
        type_folders = {TYPE_FOLDERS.get(t, t) for t in (types or [])}

        for folder_name in dict.fromkeys(TYPE_FOLDERS.values()):
            if types and folder_name not in type_folders:
                continue
            folder = self.base / folder_name
            if not folder.exists():
                continue
            for path in folder.glob("*.md"):
                fm, body = _read_note(path)
                title = fm.get("title", path.stem)
                text = f"{title}\n{body}".lower()
                if not query_lower or query_lower in text:
                    fm["id"] = str(path.relative_to(self.vault_path))
                    results.append(fm)
                    if len(results) >= limit:
                        return results
        return results

    def list_objects(self, type_key: str | None = None, limit: int = 50) -> list[dict]:
        """List notes, optionally filtered by type."""
        return self.search_objects(query="", types=[type_key] if type_key else None, limit=limit)

    # --- Types & Properties (no-op for filesystem vault) ---

    def list_types(self) -> list[dict]:
        return []

    def create_type(self, name: str, icon: str = "", layout: str = "basic") -> str | None:
        return None

    def list_properties(self) -> list[dict]:
        return []

    def create_property(self, name: str, format_type: str) -> str | None:
        return None

    # --- Convenience methods ---

    def log_completed_task(self, title: str, completed_at: datetime | None = None) -> str | None:
        """Log a completed task as a note in the recaps folder."""
        now = completed_at or datetime.now(self.tz)
        return self.create_object(
            type_key="page",
            name=f"Concluida - {title} - {now.strftime('%Y-%m-%d')}",
            description=f"Concluida em {now.strftime('%d/%m/%Y %H:%M')}",
            properties=[{"key": "done", "checkbox": True}],
        )

    def create_task(
        self,
        title: str,
        due_date: str | None = None,
        tags: list[str] | None = None,
        done: bool = False,
    ) -> str | None:
        """Create a task note."""
        props: list[dict] = [{"key": "done", "checkbox": done}]
        if due_date:
            props.append({"key": "due_date", "date": due_date})
        if tags:
            props.append({"key": "tags", "multi_select": tags})
        return self.create_object(
            type_key="task",
            name=title,
            properties=props,
        )

    def create_appointment(
        self,
        title: str,
        start_iso: str,
        end_iso: str,
        location: str = "",
        calendar_event_id: str = "",
        recurring: bool = False,
        description: str = "",
    ) -> str | None:
        """Create an appointment note with structured properties."""
        props: list[dict] = [
            {"key": "start", "date": start_iso},
            {"key": "end", "date": end_iso},
        ]
        if location:
            props.append({"key": "location", "text": location})
        if calendar_event_id:
            props.append({"key": "calendar_event_id", "text": calendar_event_id})
        if recurring:
            props.append({"key": "recurring", "checkbox": True})
        return self.create_object(
            type_key="compromisso",
            name=title,
            description=description[:200] if description else "",
            properties=props,
        )

    def update_appointment(
        self,
        object_id: str,
        title: str | None = None,
        start_iso: str | None = None,
        end_iso: str | None = None,
        location: str | None = None,
        description: str | None = None,
    ) -> bool:
        """Patch an appointment note's frontmatter."""
        kwargs: dict = {}
        if title is not None:
            kwargs["name"] = title
        if description is not None:
            kwargs["description"] = description[:200]

        props: list[dict] = []
        if start_iso is not None:
            props.append({"key": "start", "date": start_iso})
        if end_iso is not None:
            props.append({"key": "end", "date": end_iso})
        if location is not None:
            props.append({"key": "location", "text": location})
        if props:
            kwargs["properties"] = props

        return self.update_object(object_id, **kwargs)

    def save_daily_recap(self, date: str, summary: str) -> str | None:
        """Save a daily recap as a page note."""
        return self.create_object(
            type_key="page",
            name=f"Resumo - {date}",
            body=summary,
            description=summary[:200],
        )

    def save_note(self, title: str, body: str, tags: list[str] | None = None) -> str | None:
        """Save a quick note."""
        props = [{"key": "tags", "multi_select": tags}] if tags else None
        return self.create_object(
            type_key="note",
            name=title,
            body=body,
            properties=props,
        )

    # --- Classification helpers ---

    def list_unclassified(self, type_keys: list[str] | None = None, limit: int = 200) -> list[dict]:
        """Return notes that have no classified_at frontmatter set."""
        if type_keys is None:
            type_keys = ["tarefa", "nota_rapida", "compromisso"]

        out: list[dict] = []
        for tk in type_keys:
            folder_name = TYPE_FOLDERS.get(tk, tk)
            folder = self.base / folder_name
            if not folder.exists():
                continue
            for path in folder.glob("*.md"):
                fm, body = _read_note(path)
                if not fm.get("classified_at"):
                    fm["_type_key"] = tk
                    fm["id"] = str(path.relative_to(self.vault_path))
                    out.append(fm)
                    if len(out) >= limit:
                        return out
        return out

    def set_classification(
        self,
        object_id: str,
        area: str | None = None,
        prioridade: str | None = None,
        tags: list[str] | None = None,
        classified_at: str | None = None,
    ) -> bool:
        """Apply classification fields to a note's frontmatter."""
        props: list[dict] = []
        if area:
            props.append({"key": "area", "text": area})
        if prioridade:
            props.append({"key": "prioridade", "text": prioridade})
        if tags is not None:
            props.append({"key": "tags", "multi_select": tags})
        if classified_at:
            props.append({"key": "classified_at", "date": classified_at})
        if not props:
            return False
        return self.update_object_properties(object_id, props)

    def close(self) -> None:
        """No-op: filesystem client has no persistent connections."""
