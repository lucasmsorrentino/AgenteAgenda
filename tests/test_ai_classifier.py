"""Tests for services.ai_classifier — taxonomy clamping + batch dispatch."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.labels import AREAS, PRIORIDADES, TAGS
from services.ai_classifier import (
    BATCH_SIZE,
    _build_prompt,
    _item_line,
    clamp_to_taxonomy,
    classify_batch,
    classify_unclassified,
)


# --- clamp_to_taxonomy ---

def test_clamp_passes_valid_values():
    result = clamp_to_taxonomy({
        "area": AREAS[0], "prioridade": PRIORIDADES[0], "tags": [TAGS[0]]
    })
    assert result == {"area": AREAS[0], "prioridade": PRIORIDADES[0], "tags": [TAGS[0]]}


def test_clamp_rejects_invalid_area():
    result = clamp_to_taxonomy({"area": "bogus_area", "prioridade": "alta", "tags": []})
    assert result["area"] == "pessoal"
    assert result["prioridade"] == "alta"


def test_clamp_rejects_invalid_priority():
    result = clamp_to_taxonomy({"area": "tcc", "prioridade": "super_urgente", "tags": []})
    assert result["prioridade"] == "baixa"


def test_clamp_filters_unknown_tags():
    result = clamp_to_taxonomy({
        "area": "tcc",
        "prioridade": "media",
        "tags": [TAGS[0], "made_up_tag", TAGS[1]],
    })
    assert result["tags"] == [TAGS[0], TAGS[1]]


def test_clamp_handles_missing_keys():
    result = clamp_to_taxonomy({})
    assert result == {"area": "pessoal", "prioridade": "baixa", "tags": []}


def test_clamp_handles_none_tags():
    result = clamp_to_taxonomy({"area": "tcc", "prioridade": "media", "tags": None})
    assert result["tags"] == []


# --- _item_line / _build_prompt ---

def test_item_line_shape():
    obj = {"id": "abcdef1234xyz", "name": "Estudar redes", "snippet": "Cap 3", "_type_key": "tarefa"}
    line = _item_line(obj)
    assert line.startswith("- abcdef1234")
    assert "[tarefa]" in line
    assert "Estudar redes" in line
    assert "Cap 3" in line


def test_item_line_truncates_long_name():
    long_name = "x" * 500
    obj = {"id": "a" * 10, "name": long_name, "snippet": "", "_type_key": "nota_rapida"}
    line = _item_line(obj)
    assert len(line) < 300


def test_build_prompt_contains_taxonomy_and_items():
    items = [{"id": "i1", "name": "Tarefa X", "snippet": "", "_type_key": "tarefa"}]
    prompt = _build_prompt(items)
    assert "AREAS" in prompt
    assert "PRIORIDADES" in prompt
    assert "Tarefa X" in prompt
    assert "JSON array" in prompt.lower() or "json" in prompt.lower()


# --- classify_batch with mocked LLM ---

@pytest.mark.asyncio
async def test_classify_batch_empty_items():
    result = await classify_batch(MagicMock(), [])
    assert result == {"classified": 0, "failed": 0, "by_area": {}}


@pytest.mark.asyncio
async def test_classify_batch_applies_results():
    items = [
        {"id": "full_id_001", "name": "TCC cap 1", "snippet": "", "_type_key": "tarefa"},
        {"id": "full_id_002", "name": "Consulta", "snippet": "", "_type_key": "compromisso"},
    ]
    # LLM echoes back the 10-char prefix
    llm_response = [
        {"id": "full_id_00", "area": "tcc", "prioridade": "alta", "tags": ["estudo"]},
        {"id": "full_id_00", "area": "saude", "prioridade": "media", "tags": ["consulta"]},
    ]
    # Both items share the same 10-char prefix — only the first result wins.
    # Test with unique prefixes instead.
    items = [
        {"id": "aaaaaaaaaa_001", "name": "TCC", "snippet": "", "_type_key": "tarefa"},
        {"id": "bbbbbbbbbb_002", "name": "Consulta", "snippet": "", "_type_key": "compromisso"},
    ]
    llm_response = [
        {"id": "aaaaaaaaaa", "area": "tcc", "prioridade": "alta", "tags": ["estudo"]},
        {"id": "bbbbbbbbbb", "area": "saude", "prioridade": "media", "tags": ["consulta"]},
    ]
    client = MagicMock()
    client.set_classification.return_value = True

    with patch("services.ai_classifier.run_claude", AsyncMock(return_value=llm_response)):
        result = await classify_batch(client, items)

    assert result["classified"] == 2
    assert result["failed"] == 0
    assert result["by_area"]["tcc"] == 1
    assert result["by_area"]["saude"] == 1
    assert client.set_classification.call_count == 2


@pytest.mark.asyncio
async def test_classify_batch_handles_llm_failure():
    from services.ai_subprocess import AISubprocessError
    items = [{"id": "a" * 10, "name": "x", "snippet": "", "_type_key": "tarefa"}]
    client = MagicMock()
    with patch(
        "services.ai_classifier.run_claude",
        AsyncMock(side_effect=AISubprocessError("boom")),
    ):
        result = await classify_batch(client, items)
    assert result == {"classified": 0, "failed": 1, "by_area": {}}


@pytest.mark.asyncio
async def test_classify_batch_skips_unmatched_ids():
    items = [
        {"id": "real_id_001", "name": "x", "snippet": "", "_type_key": "tarefa"},
    ]
    # LLM returns a different id → item not applied
    llm_response = [{"id": "wrong_id", "area": "tcc", "prioridade": "media", "tags": []}]
    client = MagicMock()
    with patch("services.ai_classifier.run_claude", AsyncMock(return_value=llm_response)):
        result = await classify_batch(client, items)
    assert result["classified"] == 0
    assert result["failed"] == 1


@pytest.mark.asyncio
async def test_classify_batch_clamps_bogus_taxonomy():
    items = [{"id": "id00000001", "name": "x", "snippet": "", "_type_key": "tarefa"}]
    llm_response = [
        {"id": "id00000001", "area": "bogus", "prioridade": "bogus", "tags": ["bogus"]},
    ]
    client = MagicMock()
    client.set_classification.return_value = True
    with patch("services.ai_classifier.run_claude", AsyncMock(return_value=llm_response)):
        await classify_batch(client, items)
    # set_classification should have been called with clamped defaults
    args = client.set_classification.call_args
    assert args.kwargs["area"] == "pessoal"
    assert args.kwargs["prioridade"] == "baixa"
    assert args.kwargs["tags"] == []


# --- classify_unclassified end-to-end shape ---

@pytest.mark.asyncio
async def test_classify_unclassified_empty():
    client = MagicMock()
    client.list_unclassified.return_value = []
    result = await classify_unclassified(client)
    assert result == {"classified": 0, "failed": 0, "by_area": {}, "total": 0}


@pytest.mark.asyncio
async def test_classify_unclassified_paginates_by_batch_size():
    # Create more items than BATCH_SIZE to force multiple batches
    items = [
        {"id": f"id_{i:06d}", "name": f"item {i}", "snippet": "", "_type_key": "tarefa"}
        for i in range(BATCH_SIZE + 5)
    ]
    client = MagicMock()
    client.list_unclassified.return_value = items
    client.set_classification.return_value = True

    # Return one valid entry per item in the batch
    def fake_llm(prompt, timeout=120.0):
        # Count how many items in this prompt by counting "- id_" prefixes
        return [
            {"id": f"id_{i:06d}"[:10], "area": "tcc", "prioridade": "media", "tags": []}
            for i in range(BATCH_SIZE + 5)
            if f"id_{i:06d}" in prompt
        ]

    with patch(
        "services.ai_classifier.run_claude",
        AsyncMock(side_effect=fake_llm),
    ):
        result = await classify_unclassified(client)

    assert result["total"] == BATCH_SIZE + 5
    assert result["classified"] == BATCH_SIZE + 5
