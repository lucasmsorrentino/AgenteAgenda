"""Calendar → knowledge-store sync service.

Mirrors Google Calendar events into the knowledge store (Obsidian by default;
see integrations/knowledge.py). Calendar is the source of truth; the store is
a queryable read replica.

State file (`data/sync_state.json`) maps each Google event ID to the store
object it was mirrored to (`object_id`), plus the `updated` timestamp from
Google. This lets us detect three cases on each sync:
  - new event   → create store object
  - updated     → patch existing store object (name + description)
  - cancelled   → delete the store object

Events that fall out of the sync window are left alone (we only delete when
they were inside the window and Google no longer returns them).

Note: `object_id` was historically called `anytype_id`; _load_sync_state
migrates the legacy field on read.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

from config.settings import TIMEZONE

SYNC_STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "sync_state.json"


def _load_sync_state() -> dict:
    if SYNC_STATE_FILE.exists():
        try:
            data = json.loads(SYNC_STATE_FILE.read_text())
            # Migrate legacy format ({"synced_event_ids": [...]}) to new map
            if "events" not in data:
                data = {"events": {}, "last_sync": data.get("last_sync", "")}
            # Migrate legacy per-entry field anytype_id -> object_id
            for entry in data.get("events", {}).values():
                if isinstance(entry, dict) and "anytype_id" in entry:
                    entry.setdefault("object_id", entry.pop("anytype_id"))
            return data
        except Exception:
            return {"events": {}, "last_sync": ""}
    return {"events": {}, "last_sync": ""}


def _save_sync_state(state: dict) -> None:
    SYNC_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SYNC_STATE_FILE.write_text(json.dumps(state, indent=2))


def _build_description(ev) -> str:
    parts = [ev.start.strftime("%d/%m/%Y %H:%M")]
    if ev.end and ev.end != ev.start:
        parts.append("até " + ev.end.strftime("%H:%M"))
    if ev.location:
        parts.append(ev.location)
    if ev.recurring_event_id:
        parts.append("🔁 recorrente")
    if ev.description:
        parts.append(ev.description[:120])
    return " | ".join(parts)[:200]


async def sync_calendar_to_anytype(
    calendar_client,
    anytype_client,
    days_back: int = 7,
    days_forward: int = 60,
    only_event_ids: set[str] | None = None,
) -> dict:
    """Sync Google Calendar events to Anytype.

    If `only_event_ids` is given, restricts processing to those IDs (used for
    incremental sync after a /novo, /editar or /cancelar). Window-based delete
    detection is skipped in that mode to avoid removing untouched events.
    """
    if not calendar_client or not anytype_client:
        logger.warning("Calendar or Anytype not available for sync")
        return {"created": 0, "updated": 0, "deleted": 0, "skipped": 0, "errors": 0}

    tz = ZoneInfo(TIMEZONE)
    state = _load_sync_state()
    mapping: dict = state.get("events", {})

    try:
        events = calendar_client.get_all_events_range(days_back, days_forward)
    except Exception as e:
        logger.error("Failed to fetch calendar events for sync: {}", e)
        return {"created": 0, "updated": 0, "deleted": 0, "skipped": 0, "errors": 1}

    if only_event_ids is not None:
        events = [e for e in events if e.id in only_event_ids]

    counts = {"created": 0, "updated": 0, "deleted": 0, "skipped": 0, "errors": 0}
    seen_ids: set[str] = set()

    for ev in events:
        seen_ids.add(ev.id)
        entry = mapping.get(ev.id)

        # Unchanged: skip
        if entry and entry.get("updated") == ev.updated:
            counts["skipped"] += 1
            continue

        try:
            if entry and entry.get("object_id"):
                # Update existing
                if entry.get("type") == "compromisso":
                    ok = anytype_client.update_appointment(
                        entry["object_id"],
                        title=ev.title,
                        start_iso=ev.start.isoformat(),
                        end_iso=ev.end.isoformat(),
                        location=ev.location,
                        description=_build_description(ev),
                    )
                else:
                    ok = anytype_client.update_object(
                        entry["object_id"],
                        name=ev.title,
                        description=_build_description(ev),
                    )
                if ok:
                    entry["updated"] = ev.updated
                    entry["start_iso"] = ev.start.isoformat()
                    counts["updated"] += 1
                else:
                    counts["errors"] += 1
            else:
                # Create new
                if ev.is_todo:
                    obj_id = anytype_client.create_task(
                        title=ev.title,
                        due_date=ev.start.isoformat(),
                        done=ev.is_done,
                    )
                    type_used = "tarefa"
                else:
                    obj_id = anytype_client.create_appointment(
                        title=ev.title,
                        start_iso=ev.start.isoformat(),
                        end_iso=ev.end.isoformat(),
                        location=ev.location,
                        calendar_event_id=ev.id,
                        recurring=bool(ev.recurring_event_id),
                        description=_build_description(ev),
                    )
                    type_used = "compromisso"

                if obj_id:
                    mapping[ev.id] = {
                        "object_id": obj_id,
                        "updated": ev.updated,
                        "start_iso": ev.start.isoformat(),
                        "type": type_used,
                    }
                    counts["created"] += 1
                else:
                    counts["errors"] += 1
        except Exception as e:
            logger.error("Sync failed for event '{}': {}", ev.title, e)
            counts["errors"] += 1

    # Delete detection: entries whose start_iso is inside the window but Google
    # no longer returned them. Skip in incremental mode.
    if only_event_ids is None:
        now = datetime.now(tz)
        from datetime import timedelta
        window_start = now - timedelta(days=days_back)
        window_end = now + timedelta(days=days_forward)

        for gid in list(mapping.keys()):
            if gid in seen_ids:
                continue
            entry = mapping[gid]
            start_iso = entry.get("start_iso", "")
            try:
                start_dt = datetime.fromisoformat(start_iso) if start_iso else None
            except ValueError:
                start_dt = None
            if start_dt and window_start <= start_dt <= window_end:
                try:
                    anytype_client.delete_object(entry["object_id"])
                    counts["deleted"] += 1
                except Exception as e:
                    logger.error("Failed to delete store object {}: {}", entry["object_id"], e)
                    counts["errors"] += 1
                del mapping[gid]

    state["events"] = mapping
    state["last_sync"] = datetime.now(tz).isoformat()
    _save_sync_state(state)

    logger.info(
        "Sync done: {} created, {} updated, {} deleted, {} skipped, {} errors",
        counts["created"], counts["updated"], counts["deleted"], counts["skipped"], counts["errors"],
    )
    return counts
