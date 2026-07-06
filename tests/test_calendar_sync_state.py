"""Tests for calendar_sync state (de)serialization + legacy migration."""

from __future__ import annotations

import json

from services.calendar_sync import _load_sync_state, _save_sync_state


def test_load_missing_file_returns_default(tmp_path, monkeypatch):
    fake_file = tmp_path / "sync_state.json"
    monkeypatch.setattr("services.calendar_sync.SYNC_STATE_FILE", fake_file)

    state = _load_sync_state()
    assert state == {"events": {}, "last_sync": ""}


def test_load_legacy_format_migrates(tmp_path, monkeypatch):
    fake_file = tmp_path / "sync_state.json"
    legacy = {"synced_event_ids": ["a", "b"], "last_sync": "2026-04-10T12:00:00"}
    fake_file.write_text(json.dumps(legacy))
    monkeypatch.setattr("services.calendar_sync.SYNC_STATE_FILE", fake_file)

    state = _load_sync_state()
    assert state["events"] == {}
    assert state["last_sync"] == "2026-04-10T12:00:00"


def test_load_current_format_preserved(tmp_path, monkeypatch):
    fake_file = tmp_path / "sync_state.json"
    current = {
        "events": {
            "g1": {"object_id": "agenda/compromissos/x.md", "updated": "2026-04-10T12:00:00Z",
                   "start_iso": "2026-04-10T12:00:00", "type": "compromisso"}
        },
        "last_sync": "2026-04-10T12:05:00",
    }
    fake_file.write_text(json.dumps(current))
    monkeypatch.setattr("services.calendar_sync.SYNC_STATE_FILE", fake_file)

    state = _load_sync_state()
    assert state["events"]["g1"]["object_id"] == "agenda/compromissos/x.md"
    assert state["events"]["g1"]["type"] == "compromisso"


def test_load_legacy_anytype_id_migrates(tmp_path, monkeypatch):
    fake_file = tmp_path / "sync_state.json"
    legacy = {
        "events": {
            "g1": {"anytype_id": "a1", "updated": "2026-04-10T12:00:00Z",
                   "start_iso": "2026-04-10T12:00:00", "type": "compromisso"}
        },
        "last_sync": "2026-04-10T12:05:00",
    }
    fake_file.write_text(json.dumps(legacy))
    monkeypatch.setattr("services.calendar_sync.SYNC_STATE_FILE", fake_file)

    state = _load_sync_state()
    assert state["events"]["g1"]["object_id"] == "a1"
    assert "anytype_id" not in state["events"]["g1"]


def test_load_corrupt_file_returns_default(tmp_path, monkeypatch):
    fake_file = tmp_path / "sync_state.json"
    fake_file.write_text("not valid json {")
    monkeypatch.setattr("services.calendar_sync.SYNC_STATE_FILE", fake_file)

    state = _load_sync_state()
    assert state == {"events": {}, "last_sync": ""}


def test_save_then_load_roundtrip(tmp_path, monkeypatch):
    fake_file = tmp_path / "nested" / "sync_state.json"
    monkeypatch.setattr("services.calendar_sync.SYNC_STATE_FILE", fake_file)

    state = {
        "events": {"g1": {"object_id": "a1", "updated": "x", "start_iso": "y", "type": "t"}},
        "last_sync": "2026-04-10T12:00:00",
    }
    _save_sync_state(state)
    # Parent dir should have been created
    assert fake_file.exists()

    loaded = _load_sync_state()
    assert loaded == state


def test_build_description_for_event():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from services.calendar_sync import _build_description

    tz = ZoneInfo("America/Sao_Paulo")
    ev = type("E", (), {
        "start": datetime(2026, 5, 10, 14, 0, tzinfo=tz),
        "end": datetime(2026, 5, 10, 15, 0, tzinfo=tz),
        "location": "Sala 3",
        "recurring_event_id": "master_id",
        "description": "Detalhes" * 20,
    })()

    desc = _build_description(ev)
    assert "10/05/2026 14:00" in desc
    assert "15:00" in desc
    assert "Sala 3" in desc
    assert "🔁 recorrente" in desc
    assert len(desc) <= 200
