"""Parse natural Portuguese recurrence phrases into RFC 5545 RRULE strings.

Examples accepted:
    "diario"                              → FREQ=DAILY
    "todo dia"                            → FREQ=DAILY
    "dias uteis"                          → FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR
    "semanal"                             → FREQ=WEEKLY
    "toda segunda"                        → FREQ=WEEKLY;BYDAY=MO
    "toda seg, qua e sex"                 → FREQ=WEEKLY;BYDAY=MO,WE,FR
    "mensal"                              → FREQ=MONTHLY
    "todo mes dia 15"                     → FREQ=MONTHLY;BYMONTHDAY=15
    "anual"                               → FREQ=YEARLY
    Suffixes:
      "... ate 30/06/2026"                → adds UNTIL=20260630T235959Z
      "... 10 vezes"                      → adds COUNT=10
"""

from __future__ import annotations

import re
from datetime import datetime

DAY_MAP = {
    "seg": "MO", "segunda": "MO", "segunda-feira": "MO",
    "ter": "TU", "terca": "TU", "terça": "TU", "terca-feira": "TU", "terça-feira": "TU",
    "qua": "WE", "quarta": "WE", "quarta-feira": "WE",
    "qui": "TH", "quinta": "TH", "quinta-feira": "TH",
    "sex": "FR", "sexta": "FR", "sexta-feira": "FR",
    "sab": "SA", "sabado": "SA", "sábado": "SA",
    "dom": "SU", "domingo": "SU",
}


def _extract_until(text: str) -> tuple[str, str | None]:
    """Strip 'ate DD/MM[/YYYY]' from text, return (clean_text, UNTIL or None)."""
    m = re.search(r"\bat[eé]\s+(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", text)
    if not m:
        return text, None
    day, month, year = m.group(1), m.group(2), m.group(3)
    if not year:
        year = str(datetime.now().year)
    elif len(year) == 2:
        year = "20" + year
    until = f"{int(year):04d}{int(month):02d}{int(day):02d}T235959Z"
    return (text[: m.start()] + text[m.end() :]).strip(), until


def _extract_count(text: str) -> tuple[str, int | None]:
    m = re.search(r"(\d+)\s+vezes\b", text)
    if not m:
        return text, None
    return (text[: m.start()] + text[m.end() :]).strip(), int(m.group(1))


def _extract_monthday(text: str) -> tuple[str, int | None]:
    m = re.search(r"\bdia\s+(\d{1,2})\b", text)
    if not m:
        return text, None
    return (text[: m.start()] + text[m.end() :]).strip(), int(m.group(1))


def _extract_days(text: str) -> list[str]:
    """Find weekday tokens in text. Returns list of ['MO','WE',...] in order found."""
    tokens = re.split(r"[\s,;]+|\be\b", text)
    days: list[str] = []
    for t in tokens:
        t = t.strip().lower()
        if t in DAY_MAP and DAY_MAP[t] not in days:
            days.append(DAY_MAP[t])
    return days


def parse_recurrence(text: str) -> str | None:
    """Parse a Portuguese recurrence phrase into an RRULE string (without prefix).

    Returns None if no recurrence is recognized. The returned string is the
    rule body — caller should wrap as ['RRULE:' + rule] for Google Calendar.
    """
    if not text:
        return None
    t = text.strip().lower()
    if not t:
        return None

    t, until = _extract_until(t)
    t, count = _extract_count(t)

    parts: list[str] = []

    # Interval-based: "3 semanas", "a cada 2 dias", "2 em 2 meses", "de 3 em 3 semanas"
    m = re.search(
        r"\b(?:a\s+cada\s+|de\s+)?(\d+)(?:\s+em\s+\d+)?\s+(dias?|semanas?|m[eê]s|meses|anos?)\b",
        t,
    )
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        t = (t[: m.start()] + t[m.end() :]).strip()
        if unit.startswith("dia"):
            parts = ["FREQ=DAILY"]
        elif unit.startswith("semana"):
            parts = ["FREQ=WEEKLY"]
            days = _extract_days(t)
            if days:
                parts.append("BYDAY=" + ",".join(days))
        elif unit.startswith("m"):
            parts = ["FREQ=MONTHLY"]
            monthday = _extract_monthday(t)[1]
            if monthday:
                parts.append(f"BYMONTHDAY={monthday}")
        elif unit.startswith("ano"):
            parts = ["FREQ=YEARLY"]
        if n > 1:
            parts.insert(1, f"INTERVAL={n}")

    if parts:
        pass  # interval branch already populated `parts`
    elif re.search(r"\bdias?\s+uteis\b|\bdia\s+util\b", t):
        parts = ["FREQ=WEEKLY", "BYDAY=MO,TU,WE,TH,FR"]
    elif re.search(r"\b(diari[oa]|todo\s+dia|todos\s+os\s+dias)\b", t):
        parts = ["FREQ=DAILY"]
    elif re.search(r"\banual\b|\btodo\s+ano\b", t):
        parts = ["FREQ=YEARLY"]
    elif re.search(r"\bmensal\b|\btodo\s+m[eê]s\b", t):
        monthday = _extract_monthday(t)[1]
        parts = ["FREQ=MONTHLY"]
        if monthday:
            parts.append(f"BYMONTHDAY={monthday}")
    else:
        days = _extract_days(t)
        if days or re.search(r"\bsemanal\b|\btoda\s+semana\b", t):
            parts = ["FREQ=WEEKLY"]
            if days:
                parts.append("BYDAY=" + ",".join(days))

    if not parts:
        return None

    if until:
        parts.append(f"UNTIL={until}")
    elif count:
        parts.append(f"COUNT={count}")

    return ";".join(parts)
