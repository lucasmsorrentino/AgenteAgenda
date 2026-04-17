"""Tests for services.ai_subprocess — JSON extraction from LLM output."""

from __future__ import annotations

import pytest

from services.ai_subprocess import AISubprocessError, _extract_json


def test_plain_json_object():
    assert _extract_json('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}


def test_plain_json_array():
    assert _extract_json("[1, 2, 3]") == [1, 2, 3]


def test_json_with_prose_preamble():
    raw = 'Here is the answer:\n{"action": "create_task", "title": "X"}'
    assert _extract_json(raw) == {"action": "create_task", "title": "X"}


def test_json_with_code_fence():
    raw = '```json\n{"a": 1}\n```'
    assert _extract_json(raw) == {"a": 1}


def test_json_with_fence_no_lang():
    raw = '```\n{"a": 1}\n```'
    assert _extract_json(raw) == {"a": 1}


def test_nested_braces():
    raw = '{"outer": {"inner": {"deep": true}}}'
    assert _extract_json(raw) == {"outer": {"inner": {"deep": True}}}


def test_braces_inside_strings_are_ignored():
    # The `{` and `}` inside the string should not affect depth counting
    raw = '{"text": "a { not } counted"}'
    assert _extract_json(raw) == {"text": "a { not } counted"}


def test_escaped_quotes_in_string():
    raw = '{"text": "she said \\"hi\\""}'
    assert _extract_json(raw) == {"text": 'she said "hi"'}


def test_no_json_raises():
    with pytest.raises(AISubprocessError):
        _extract_json("just some prose with no JSON here")


def test_unterminated_raises():
    with pytest.raises(AISubprocessError):
        _extract_json('{"a": 1')


def test_invalid_json_raises():
    with pytest.raises(AISubprocessError):
        _extract_json('{"a": undefined_keyword}')


def test_array_of_objects():
    raw = '[{"id": "a"}, {"id": "b"}]'
    assert _extract_json(raw) == [{"id": "a"}, {"id": "b"}]
