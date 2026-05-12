"""Anytype REST API client.

Wraps the local Anytype API (localhost:31009) with httpx for full
control over objects, types, and properties.

API Reference: https://developers.anytype.io/
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx
from loguru import logger

from config.settings import ANYTYPE_API_KEY, ANYTYPE_API_VERSION, ANYTYPE_BASE_URL, ANYTYPE_SPACE_ID

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "data" / "anytype_schema.json"


def _load_schema() -> dict:
    """Load custom type/property keys created by setup_anytype.py.

    Returns {"types": {Name: key}, "properties": {Name: key}} or empty dict.
    """
    if SCHEMA_FILE.exists():
        try:
            return json.loads(SCHEMA_FILE.read_text())
        except Exception:
            return {}
    return {}


_SCHEMA = _load_schema()
_CUSTOM_COMPROMISSO = _SCHEMA.get("types", {}).get("Compromisso")

# Map our logical names to Anytype built-in types (layout-integrated)
# Built-in types show up in native Anytype views automatically.
TYPE_MAP = {
    "tarefa": "task",         # layout=action, has done/due_date/status/tag
    "nota_rapida": "note",    # layout=note, native note type
    "resumo_diario": "page",  # layout=basic, daily recaps as pages
    # Resolved at import time: prefer custom Compromisso type from schema; fall
    # back to built-in `page` if the user hasn't run setup_anytype.py yet.
    "compromisso": _CUSTOM_COMPROMISSO or "page",
    "page": "page",           # pass-through
    "task": "task",           # pass-through
    "note": "note",           # pass-through
}


class AnytypeClient:
    """Client for the Anytype local REST API."""

    def __init__(
        self,
        base_url: str = ANYTYPE_BASE_URL,
        api_key: str = ANYTYPE_API_KEY,
        api_version: str = ANYTYPE_API_VERSION,
        space_id: str = ANYTYPE_SPACE_ID,
    ):
        self.base_url = base_url.rstrip("/")
        self.space_id = space_id
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Anytype-Version": api_version,
            "Content-Type": "application/json",
        }
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url,
                headers=self.headers,
                timeout=15.0,
            )
        return self._client

    def _space_url(self, path: str) -> str:
        """Build a URL path scoped to the current space."""
        return f"/v1/spaces/{self.space_id}/{path}"

    # --- Connection ---

    def verify_connection(self) -> bool:
        """Check if Anytype API is reachable and authenticated."""
        try:
            resp = self.client.get("/v1/spaces")
            resp.raise_for_status()
            spaces = resp.json().get("data", [])
            logger.info("Anytype connected: {} space(s) found", len(spaces))
            return True
        except Exception as e:
            logger.warning("Anytype connection failed: {}", e)
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
        """Create an object in Anytype. Returns object_id or None.

        type_key is mapped through TYPE_MAP (e.g. 'tarefa' -> 'task').
        The Anytype API accepts: name, type_key, description, body, icon.
        After creation, properties can be set via update_object_properties().
        """
        resolved_type = TYPE_MAP.get(type_key, type_key)
        payload: dict = {
            "name": name,
            "type_key": resolved_type,
        }
        if body:
            payload["body"] = body
        if description:
            payload["description"] = description[:200]
        if icon:
            payload["icon"] = {"format": "emoji", "emoji": icon}

        try:
            resp = self.client.post(self._space_url("objects"), json=payload)
            resp.raise_for_status()
            obj = resp.json().get("object", {})
            object_id = obj.get("id", "")
            logger.info("Created Anytype object: {} ({}) [type={}]", name, object_id, resolved_type)

            # Set additional properties if provided
            if properties and object_id:
                self.update_object_properties(object_id, properties)

            return object_id
        except Exception as e:
            logger.error("Failed to create Anytype object '{}': {}", name, e)
            return None

    def update_object_properties(self, object_id: str, properties: list) -> bool:
        """Update properties on an object.

        properties: list of dicts like:
            [{"key": "done", "checkbox": True},
             {"key": "due_date", "date": "2026-04-15"},
             {"key": "tag", "multi_select": [{"name": "TCC"}]}]
        """
        try:
            resp = self.client.patch(
                self._space_url(f"objects/{object_id}"),
                json={"properties": properties},
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error("Failed to update properties on {}: {}", object_id, e)
            return False

    def get_object(self, object_id: str) -> dict | None:
        """Get an object by ID."""
        try:
            resp = self.client.get(self._space_url(f"objects/{object_id}"))
            resp.raise_for_status()
            return resp.json().get("object")
        except Exception as e:
            logger.error("Failed to get Anytype object {}: {}", object_id, e)
            return None

    def update_object(self, object_id: str, **kwargs) -> bool:
        """Update an object. Pass name=, body=, properties=, etc."""
        try:
            resp = self.client.patch(
                self._space_url(f"objects/{object_id}"), json=kwargs
            )
            resp.raise_for_status()
            logger.info("Updated Anytype object: {}", object_id)
            return True
        except Exception as e:
            logger.error("Failed to update Anytype object {}: {}", object_id, e)
            return False

    def delete_object(self, object_id: str) -> bool:
        """Archive/delete an object."""
        try:
            resp = self.client.delete(self._space_url(f"objects/{object_id}"))
            resp.raise_for_status()
            logger.info("Deleted Anytype object: {}", object_id)
            return True
        except Exception as e:
            logger.error("Failed to delete Anytype object {}: {}", object_id, e)
            return False

    # --- Search / Query ---

    def search_objects(self, query: str = "", types: list[str] | None = None, limit: int = 50) -> list[dict]:
        """Search objects by text query and optional type filter."""
        try:
            params: dict = {"query": query, "limit": limit}
            if types:
                params["types"] = types
            resp = self.client.post(self._space_url("search"), json=params)
            resp.raise_for_status()
            return resp.json().get("data", [])
        except Exception as e:
            logger.error("Anytype search failed: {}", e)
            return []

    def list_objects(self, type_key: str | None = None, limit: int = 50) -> list[dict]:
        """List objects, optionally filtered by type.

        type_key is mapped through TYPE_MAP (e.g. 'tarefa' -> 'task').
        Uses POST /search when type_key is given (GET /objects doesn't support type filtering).
        Falls back to GET /objects for unfiltered listing.
        """
        try:
            if type_key:
                resolved_type = TYPE_MAP.get(type_key, type_key)
                # GET /objects doesn't accept type_key — use search endpoint
                payload: dict = {"query": "", "types": [resolved_type], "limit": limit}
                resp = self.client.post(self._space_url("search"), json=payload)
            else:
                resp = self.client.get(self._space_url("objects"), params={"limit": limit})
            resp.raise_for_status()
            return resp.json().get("data", [])
        except Exception as e:
            logger.error("Anytype list failed: {}", e)
            return []

    # --- Types & Properties ---

    def list_types(self) -> list[dict]:
        """List all types in the space."""
        try:
            resp = self.client.get(self._space_url("types"))
            resp.raise_for_status()
            return resp.json().get("data", [])
        except Exception as e:
            logger.error("Failed to list Anytype types: {}", e)
            return []

    def create_type(self, name: str, icon: str = "", layout: str = "basic") -> str | None:
        """Create a new type. Returns type key or None."""
        try:
            resp = self.client.post(
                self._space_url("types"),
                json={"name": name, "icon": icon, "layout": layout},
            )
            resp.raise_for_status()
            type_data = resp.json().get("type", {})
            type_key = type_data.get("key", "")
            logger.info("Created Anytype type: {} ({})", name, type_key)
            return type_key
        except Exception as e:
            logger.error("Failed to create Anytype type '{}': {}", name, e)
            return None

    def list_properties(self) -> list[dict]:
        """List all properties in the space."""
        try:
            resp = self.client.get(self._space_url("properties"))
            resp.raise_for_status()
            return resp.json().get("data", [])
        except Exception as e:
            logger.error("Failed to list Anytype properties: {}", e)
            return []

    def create_property(self, name: str, format_type: str) -> str | None:
        """Create a property. format_type: text, number, date, select, multi_select, checkbox, etc."""
        try:
            resp = self.client.post(
                self._space_url("properties"),
                json={"name": name, "format": format_type},
            )
            resp.raise_for_status()
            prop_data = resp.json().get("property", {})
            prop_key = prop_data.get("key", "")
            logger.info("Created Anytype property: {} ({})", name, prop_key)
            return prop_key
        except Exception as e:
            logger.error("Failed to create Anytype property '{}': {}", name, e)
            return None

    # --- Convenience methods ---

    def log_completed_task(self, title: str, completed_at: datetime | None = None) -> str | None:
        """Log a completed task to Anytype (native Task type with done=True)."""
        now = completed_at or datetime.now()
        return self.create_object(
            type_key="task",
            name=title,
            description=f"Concluida em {now.strftime('%d/%m/%Y %H:%M')}",
            properties=[
                {"key": "done", "checkbox": True},
            ],
        )

    def create_task(
        self,
        title: str,
        due_date: str | None = None,
        tags: list[str] | None = None,
        done: bool = False,
    ) -> str | None:
        """Create a task in Anytype with native Task type properties.

        Args:
            title: Task name
            due_date: ISO date string (e.g. '2026-04-15' or '2026-04-15T12:00:00Z')
            tags: List of tag names — applied only if they already exist as tag options
            done: Whether the task is already completed
        """
        props: list[dict] = [{"key": "done", "checkbox": done}]
        if due_date:
            props.append({"key": "due_date", "date": due_date})

        obj_id = self.create_object(
            type_key="task",
            name=title,
            properties=props,
        )

        # Tags require pre-existing options — attempt and ignore errors
        if tags and obj_id:
            try:
                self.update_object_properties(
                    obj_id, [{"key": "tag", "multi_select": tags}]
                )
            except Exception:
                logger.debug("Could not set tags on task {} (tags may not exist yet)", obj_id)

        return obj_id

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
        """Create a Compromisso object with structured properties.

        Falls back gracefully: if the custom Compromisso type / properties don't
        exist (setup_anytype.py wasn't run), this still creates a `page` with the
        info in the description.
        """
        prop_map = _SCHEMA.get("properties", {}) if _SCHEMA else {}
        props: list[dict] = []
        if prop_map.get("start"):
            props.append({"key": prop_map["start"], "date": start_iso})
        if prop_map.get("end"):
            props.append({"key": prop_map["end"], "date": end_iso})
        if location and prop_map.get("location"):
            props.append({"key": prop_map["location"], "text": location})
        if calendar_event_id and prop_map.get("calendar_event_id"):
            props.append({"key": prop_map["calendar_event_id"], "text": calendar_event_id})
        if prop_map.get("recurring"):
            props.append({"key": prop_map["recurring"], "checkbox": recurring})

        return self.create_object(
            type_key="compromisso",
            name=title,
            description=description[:200] if description else "",
            properties=props or None,
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
        """Patch a Compromisso object's name + structured properties."""
        prop_map = _SCHEMA.get("properties", {}) if _SCHEMA else {}
        ok = True
        update_fields: dict = {}
        if title is not None:
            update_fields["name"] = title
        if description is not None:
            update_fields["description"] = description[:200]
        if update_fields:
            ok &= self.update_object(object_id, **update_fields)

        props: list[dict] = []
        if start_iso is not None and prop_map.get("start"):
            props.append({"key": prop_map["start"], "date": start_iso})
        if end_iso is not None and prop_map.get("end"):
            props.append({"key": prop_map["end"], "date": end_iso})
        if location is not None and prop_map.get("location"):
            props.append({"key": prop_map["location"], "text": location})
        if props:
            ok &= self.update_object_properties(object_id, props)
        return ok

    def save_daily_recap(self, date: str, summary: str) -> str | None:
        """Save a daily recap to Anytype as a Page."""
        return self.create_object(
            type_key="page",
            name=f"Resumo - {date}",
            body=summary,
            description=summary[:200],
        )

    def save_note(self, title: str, body: str, tags: list[str] | None = None) -> str | None:
        """Save a quick note to Anytype (native Note type)."""
        return self.create_object(
            type_key="note",
            name=title,
            body=body if body else "",
        )

    # --- Classification helpers ---

    def list_unclassified(self, type_keys: list[str] | None = None, limit: int = 200) -> list[dict]:
        """Return objects that have no classified_at date set.

        Anytype's local search doesn't support "property is empty" filters, so
        we fetch and filter client-side. Defaults to scanning tasks, notes, and
        compromisso (the three types the classifier cares about).
        """
        prop_map = _SCHEMA.get("properties", {}) if _SCHEMA else {}
        classified_key = prop_map.get("classified_at")
        if not classified_key:
            logger.warning("classified_at property not in schema — run setup to add it")
            return []

        if type_keys is None:
            type_keys = ["tarefa", "nota_rapida", "compromisso"]

        out: list[dict] = []
        for tk in type_keys:
            resolved = TYPE_MAP.get(tk, tk)
            try:
                resp = self.client.post(
                    self._space_url("search"),
                    json={"query": "", "types": [resolved], "limit": limit},
                )
                resp.raise_for_status()
                for obj in resp.json().get("data", []):
                    # properties is a list of {key, date|text|...} entries
                    props = obj.get("properties", []) or []
                    has_classified = any(
                        p.get("key") == classified_key and p.get("date")
                        for p in props
                    )
                    if not has_classified:
                        obj["_type_key"] = tk
                        out.append(obj)
            except Exception as e:
                logger.warning("Failed to list unclassified for type {}: {}", tk, e)
        return out

    def set_classification(
        self,
        object_id: str,
        area: str | None = None,
        prioridade: str | None = None,
        tags: list[str] | None = None,
        classified_at: str | None = None,
    ) -> bool:
        """Apply classification to an object.

        Silently skips any field whose corresponding property key isn't in the
        schema (so callers don't need to check). Returns True if at least one
        update was attempted and succeeded.
        """
        prop_map = _SCHEMA.get("properties", {}) if _SCHEMA else {}
        props: list[dict] = []

        if area and prop_map.get("area"):
            props.append({"key": prop_map["area"], "select": {"name": area}})
        if prioridade and prop_map.get("prioridade"):
            props.append({"key": prop_map["prioridade"], "select": {"name": prioridade}})
        if tags is not None and prop_map.get("tags"):
            props.append(
                {"key": prop_map["tags"], "multi_select": [{"name": t} for t in tags]}
            )
        if classified_at and prop_map.get("classified_at"):
            props.append({"key": prop_map["classified_at"], "date": classified_at})

        if not props:
            return False
        return self.update_object_properties(object_id, props)

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None
