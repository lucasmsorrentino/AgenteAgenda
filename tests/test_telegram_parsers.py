"""Tests for pure parsers inside ProductivityBot.

These test only methods that don't need a running bot/application —
deadline parser, /novo parser, /editar field parser.
"""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

# Ensure we don't try to read a .env with real secrets during import
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "")
os.environ.setdefault("TELEGRAM_CHAT_ID", "")

from integrations.telegram_bot import ProductivityBot


@pytest.fixture
def bot():
    return ProductivityBot()


@pytest.fixture
def tz():
    return ZoneInfo("America/Sao_Paulo")


# --- _parse_deadline ---

def test_parse_deadline_hhmm(bot, tz):
    now = datetime.now(tz)
    title, due = bot._parse_deadline("reuniao @14:30")
    assert title == "reuniao"
    assert due is not None
    assert due.hour == 14 and due.minute == 30
    # Due should be today or tomorrow (never in the past)
    assert due >= now or (due - now).total_seconds() > -60


def test_parse_deadline_date_only(bot):
    title, due = bot._parse_deadline("entrega @15/06")
    assert title == "entrega"
    assert due is not None
    assert due.day == 15 and due.month == 6


def test_parse_deadline_date_time(bot):
    title, due = bot._parse_deadline("consulta @15/06 10:30")
    assert title == "consulta"
    assert due is not None
    assert due.day == 15 and due.month == 6
    assert due.hour == 10 and due.minute == 30


def test_parse_deadline_full_date(bot):
    title, due = bot._parse_deadline("formatura @15/12/2027 19:00")
    assert title == "formatura"
    assert due is not None
    assert due.year == 2027 and due.month == 12 and due.day == 15


def test_parse_deadline_amanha(bot):
    title, due = bot._parse_deadline("x @amanha 09:00")
    assert title == "x"
    assert due is not None
    assert due.hour == 9 and due.minute == 0


def test_parse_deadline_no_deadline(bot):
    title, due = bot._parse_deadline("tarefa sem prazo")
    assert title == "tarefa sem prazo"
    assert due is None


# --- _parse_novo ---

def test_parse_novo_basic(bot):
    result = bot._parse_novo("reuniao @15/06 10:00")
    assert result is not None
    title, start, end, location, rrule = result
    assert title == "reuniao"
    assert start.hour == 10
    # Default end is start + 1h
    assert end.hour == 11
    assert location == ""
    assert rrule is None


def test_parse_novo_with_ate(bot):
    result = bot._parse_novo("reuniao @15/06 10:00 ate 11:30")
    assert result is not None
    _, start, end, _, _ = result
    assert start.hour == 10
    assert end.hour == 11 and end.minute == 30


def test_parse_novo_with_location(bot):
    result = bot._parse_novo("dentista @15/06 14:00 em Clinica X")
    assert result is not None
    _, _, _, location, _ = result
    assert location == "Clinica X"


def test_parse_novo_with_recurrence(bot):
    result = bot._parse_novo("academia @07:00 ate 08:00 repete seg, qua, sex")
    assert result is not None
    _, _, _, _, rrule = result
    assert rrule == "FREQ=WEEKLY;BYDAY=MO,WE,FR"


def test_parse_novo_full_combo(bot):
    result = bot._parse_novo(
        "terapia @amanha 15:00 ate 16:00 em Consultorio repete semanal ate 30/12/2026"
    )
    assert result is not None
    title, _, _, location, rrule = result
    assert title == "terapia"
    assert location == "Consultorio"
    assert "FREQ=WEEKLY" in rrule
    assert "UNTIL=" in rrule


def test_parse_novo_no_time_returns_none(bot):
    # Without a parseable @time marker, start is None → parse fails
    assert bot._parse_novo("reuniao sem horario") is None


def test_parse_novo_end_wraps_to_next_day(bot):
    # When "ate HH:MM" is earlier than start, end rolls to next day
    result = bot._parse_novo("vigilia @22:00 ate 02:00")
    assert result is not None
    _, start, end, _, _ = result
    assert end > start


# --- _parse_editar_fields ---

def test_parse_editar_all_fields(bot):
    fields = bot._parse_editar_fields(
        "titulo=Nova reuniao inicio=15/06 14:00 fim=15:30 local=Sala 2"
    )
    assert fields["titulo"] == "Nova reuniao"
    assert fields["inicio"] == "15/06 14:00"
    assert fields["fim"] == "15:30"
    assert fields["local"] == "Sala 2"


def test_parse_editar_single_field(bot):
    fields = bot._parse_editar_fields("titulo=Academia matinal")
    assert fields == {"titulo": "Academia matinal"}


def test_parse_editar_order_insensitive(bot):
    fields = bot._parse_editar_fields("local=X titulo=Y")
    assert fields["local"] == "X"
    assert fields["titulo"] == "Y"


def test_parse_editar_empty_returns_empty(bot):
    assert bot._parse_editar_fields("") == {}


def test_parse_editar_unknown_keys_ignored(bot):
    fields = bot._parse_editar_fields("foo=bar titulo=X")
    assert "foo" not in fields
    assert fields.get("titulo") == "X"
