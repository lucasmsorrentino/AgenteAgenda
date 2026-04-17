"""Tests for services.recurrence — pt-BR phrase → RRULE parser."""

from __future__ import annotations

import pytest

from services.recurrence import parse_recurrence


@pytest.mark.parametrize(
    "phrase, expected",
    [
        ("diario", "FREQ=DAILY"),
        ("todo dia", "FREQ=DAILY"),
        ("todos os dias", "FREQ=DAILY"),
        ("dias uteis", "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
        ("semanal", "FREQ=WEEKLY"),
        ("toda semana", "FREQ=WEEKLY"),
        ("mensal", "FREQ=MONTHLY"),
        ("anual", "FREQ=YEARLY"),
        ("todo ano", "FREQ=YEARLY"),
    ],
)
def test_basic_frequencies(phrase, expected):
    assert parse_recurrence(phrase) == expected


@pytest.mark.parametrize(
    "phrase, expected",
    [
        ("toda segunda", "FREQ=WEEKLY;BYDAY=MO"),
        ("toda seg", "FREQ=WEEKLY;BYDAY=MO"),
        ("seg, qua, sex", "FREQ=WEEKLY;BYDAY=MO,WE,FR"),
        ("segunda e quarta", "FREQ=WEEKLY;BYDAY=MO,WE"),
        ("terça", "FREQ=WEEKLY;BYDAY=TU"),
    ],
)
def test_weekday_lists(phrase, expected):
    assert parse_recurrence(phrase) == expected


def test_monthly_with_day():
    assert parse_recurrence("todo mes dia 15") == "FREQ=MONTHLY;BYMONTHDAY=15"
    assert parse_recurrence("mensal dia 1") == "FREQ=MONTHLY;BYMONTHDAY=1"


@pytest.mark.parametrize(
    "phrase, expected",
    [
        # Interval-based: "3 semanas" → INTERVAL=3
        ("3 semanas", "FREQ=WEEKLY;INTERVAL=3"),
        ("a cada 2 dias", "FREQ=DAILY;INTERVAL=2"),
        ("de 3 em 3 semanas", "FREQ=WEEKLY;INTERVAL=3"),
        ("2 meses", "FREQ=MONTHLY;INTERVAL=2"),
        ("a cada 4 anos", "FREQ=YEARLY;INTERVAL=4"),
    ],
)
def test_interval_phrases(phrase, expected):
    assert parse_recurrence(phrase) == expected


def test_interval_with_weekday():
    # "3 semanas seg" → interval 3 with weekday filter
    result = parse_recurrence("3 semanas seg, qua")
    assert result == "FREQ=WEEKLY;INTERVAL=3;BYDAY=MO,WE"


def test_until_suffix():
    result = parse_recurrence("semanal ate 30/06/2026")
    assert result == "FREQ=WEEKLY;UNTIL=20260630T235959Z"


def test_until_short_year():
    result = parse_recurrence("diario ate 30/06/26")
    assert result == "FREQ=DAILY;UNTIL=20260630T235959Z"


def test_count_suffix():
    result = parse_recurrence("diario 10 vezes")
    assert result == "FREQ=DAILY;COUNT=10"


def test_until_beats_count():
    # When both suffixes present, UNTIL wins
    result = parse_recurrence("diario 5 vezes ate 30/06/2026")
    assert "UNTIL=20260630T235959Z" in result
    assert "COUNT" not in result


def test_empty_input_returns_none():
    assert parse_recurrence("") is None
    assert parse_recurrence("   ") is None
    assert parse_recurrence(None) is None


def test_unrecognized_returns_none():
    assert parse_recurrence("ola mundo") is None
    assert parse_recurrence("xyz abc") is None
