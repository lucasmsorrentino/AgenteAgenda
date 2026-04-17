"""Tests for services.ai_parser — LLM parse envelope + fallback."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.ai_parser import parse_ia_message
from services.ai_subprocess import AISubprocessError


@pytest.mark.asyncio
async def test_parse_returns_action_dict():
    fake_response = {
        "action": "create_task",
        "title": "Estudar redes",
        "tags": ["estudo"],
        "area": "tcc",
        "prioridade": "alta",
        "reply": "Tarefa criada",
    }
    with patch("services.ai_parser.run_claude", AsyncMock(return_value=fake_response)):
        result = await parse_ia_message("anota estudar redes")
    assert result["action"] == "create_task"
    assert result["title"] == "Estudar redes"


@pytest.mark.asyncio
async def test_parse_missing_action_defaults_to_unknown():
    fake_response = {"reply": "hmm"}
    with patch("services.ai_parser.run_claude", AsyncMock(return_value=fake_response)):
        result = await parse_ia_message("algo confuso")
    assert result["action"] == "unknown"


@pytest.mark.asyncio
async def test_parse_rejects_non_dict_result():
    with patch("services.ai_parser.run_claude", AsyncMock(return_value=["list", "not", "dict"])):
        with pytest.raises(AISubprocessError):
            await parse_ia_message("x")


@pytest.mark.asyncio
async def test_parse_propagates_subprocess_error():
    with patch(
        "services.ai_parser.run_claude",
        AsyncMock(side_effect=AISubprocessError("claude crashed")),
    ):
        with pytest.raises(AISubprocessError):
            await parse_ia_message("x")
