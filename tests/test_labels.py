"""Tests for config.labels taxonomy."""

from __future__ import annotations

from config.labels import AREAS, PRIORIDADES, TAGS, taxonomy_prompt_block


def test_areas_nonempty_unique():
    assert len(AREAS) > 0
    assert len(AREAS) == len(set(AREAS))


def test_prioridades_expected_values():
    assert set(PRIORIDADES) == {"alta", "media", "baixa"}


def test_tags_nonempty_unique():
    assert len(TAGS) > 0
    assert len(TAGS) == len(set(TAGS))


def test_prompt_block_contains_all_areas():
    block = taxonomy_prompt_block()
    for area in AREAS:
        assert area in block
    for pri in PRIORIDADES:
        assert pri in block


def test_prompt_block_mentions_sections():
    block = taxonomy_prompt_block()
    assert "AREAS" in block
    assert "PRIORIDADES" in block
    assert "TAGS" in block
