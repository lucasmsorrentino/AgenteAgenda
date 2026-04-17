"""Tests for services.ai_search — context shaping + response normalization."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from services.ai_search import _format_anytype_item, _format_event, search


def test_format_anytype_item_minimal():
    obj = {"id": "abcdef1234xyz", "name": "Nota X", "snippet": "texto", "properties": []}
    line = _format_anytype_item(obj)
    assert line.startswith("abcdef1234")
    assert "Nota X" in line
    assert "texto" in line


def test_format_anytype_item_with_properties():
    obj = {
        "id": "aaaa111122xxx",
        "name": "TCC capitulo 2",
        "snippet": "",
        "properties": [
            {"key": "area", "select": {"name": "tcc"}},
            {"key": "tags", "multi_select": [{"name": "estudo"}, {"name": "leitura"}]},
            {"key": "due_date", "date": "2026-05-10"},
        ],
    }
    line = _format_anytype_item(obj)
    assert "area=tcc" in line
    assert "tags=estudo,leitura" in line
    assert "due=2026-05-10" in line


def test_format_event_shape():
    tz = ZoneInfo("America/Sao_Paulo")
    ev = SimpleNamespace(
        id="event_id_abc123xyz",
        title="Reuniao",
        start=datetime(2026, 5, 10, 14, 0, tzinfo=tz),
        location="Sala 3",
        recurring_event_id=None,
    )
    line = _format_event(ev)
    assert "event_id_" in line
    assert "Reuniao" in line
    assert "Sala 3" in line
    assert "2026-05-10 14:00" in line


def test_format_event_marks_recurring():
    tz = ZoneInfo("America/Sao_Paulo")
    ev = SimpleNamespace(
        id="r1234567890",
        title="Standup",
        start=datetime(2026, 5, 10, 9, 0, tzinfo=tz),
        location="",
        recurring_event_id="master_id",
    )
    assert "[recorrente]" in _format_event(ev)


@pytest.mark.asyncio
async def test_search_normalizes_response():
    fake = {"answer": "Voce tem 3 compromissos.", "cited_ids": ["abc1", "def2"]}
    with patch("services.ai_search.run_claude", AsyncMock(return_value=fake)):
        result = await search("o que tenho hoje?", anytype_client=None, calendar_client=None)
    assert result["answer"] == "Voce tem 3 compromissos."
    assert result["cited_ids"] == ["abc1", "def2"]


@pytest.mark.asyncio
async def test_search_handles_missing_fields():
    with patch("services.ai_search.run_claude", AsyncMock(return_value={})):
        result = await search("pergunta", None, None)
    assert result["answer"] == ""
    assert result["cited_ids"] == []


@pytest.mark.asyncio
async def test_search_handles_subprocess_error():
    from services.ai_subprocess import AISubprocessError
    with patch(
        "services.ai_search.run_claude",
        AsyncMock(side_effect=AISubprocessError("timeout")),
    ):
        result = await search("pergunta", None, None)
    assert "Erro" in result["answer"]
    assert result["cited_ids"] == []


@pytest.mark.asyncio
async def test_search_handles_invalid_response_type():
    with patch("services.ai_search.run_claude", AsyncMock(return_value=["not", "a", "dict"])):
        result = await search("pergunta", None, None)
    assert result["cited_ids"] == []
