"""Morning summary service.

Composes a daily briefing message with today's events and pending tasks.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger

from config.settings import TIMEZONE

WEEKDAYS_PT = {
    0: "Segunda-feira",
    1: "Terca-feira",
    2: "Quarta-feira",
    3: "Quinta-feira",
    4: "Sexta-feira",
    5: "Sabado",
    6: "Domingo",
}


async def generate_morning_summary(calendar_client=None, anytype_client=None) -> str:
    """Generate the morning summary text.

    Fetches today's events and pending todos from Google Calendar.
    Optionally checks Anytype for additional pending tasks.
    """
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    weekday = WEEKDAYS_PT.get(now.weekday(), "")
    date_str = now.strftime(f"%d/%m/%Y ({weekday})")

    lines = [
        f"☀️ <b>Bom dia! {date_str}</b>",
        "",
    ]

    # --- Google Calendar events ---
    events = []
    todos = []
    if calendar_client:
        try:
            all_events = calendar_client.get_today_events()
            events = [e for e in all_events if not e.is_todo]
            todos_raw = [e for e in all_events if e.is_todo]
            todos = todos_raw
        except Exception as e:
            logger.error("Morning summary — calendar error: {}", e)
            lines.append("⚠️ Erro ao buscar Google Calendar")

    if events:
        lines.append("🗓 <b>Agenda do Dia:</b>")
        for ev in events:
            if ev.is_all_day:
                lines.append(f"  • {ev.title} (dia todo)")
            else:
                start = ev.start.strftime("%H:%M")
                end = ev.end.strftime("%H:%M")
                lines.append(f"  • {start}-{end} {ev.title}")
                if ev.location:
                    lines.append(f"    📍 {ev.location}")
    else:
        lines.append("🗓 Nenhum evento agendado para hoje")

    lines.append("")

    # --- Pending tasks ---
    if todos:
        lines.append("✅ <b>Tarefas Pendentes:</b>")
        for i, td in enumerate(todos, 1):
            due = td.start.strftime("%H:%M") if not td.is_all_day else "sem horario"
            lines.append(f"  {i}. {td.title} ({due})")
    else:
        lines.append("✅ Sem tarefas pendentes — dia livre!")

    lines.append("")
    lines.append("💡 Use /add para criar novas tarefas")

    return "\n".join(lines)
