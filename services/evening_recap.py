"""Evening recap service.

Compiles what was done today, what was missed, and saves to Anytype.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger

from config.settings import TIMEZONE


async def generate_evening_recap(calendar_client=None, anytype_client=None) -> str:
    """Generate the evening recap text and optionally save to Anytype."""
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    date_str = now.strftime("%d/%m/%Y")

    lines = [
        f"🌙 <b>Resumo do Dia — {date_str}</b>",
        "",
    ]

    events_total = 0
    done_tasks = []
    pending_tasks = []

    if calendar_client:
        try:
            all_events = calendar_client.get_today_events()
            regular_events = [e for e in all_events if not e.is_todo]
            todos = [e for e in all_events if e.is_todo]
            events_total = len(regular_events)

            for td in todos:
                # [DONE] prefix means completed
                # We check the raw title from the original event
                if td.title.startswith("[DONE]") or td.title.startswith("DONE"):
                    done_tasks.append(td.title.replace("[DONE]", "").strip())
                else:
                    # Check if the task time has passed
                    if td.start < now:
                        pending_tasks.append(td.title)
                    else:
                        # Future task — still pending but not missed
                        pending_tasks.append(td.title)
        except Exception as e:
            logger.error("Evening recap — calendar error: {}", e)
            lines.append("⚠️ Erro ao buscar Google Calendar")

    # Stats
    lines.append(f"📊 <b>Estatisticas:</b>")
    lines.append(f"  • Eventos: {events_total}")
    lines.append(f"  • Tarefas concluidas: {len(done_tasks)}")
    lines.append(f"  • Tarefas pendentes: {len(pending_tasks)}")
    lines.append("")

    if done_tasks:
        lines.append("✅ <b>Concluidas:</b>")
        for t in done_tasks:
            lines.append(f"  • {t}")
        lines.append("")

    if pending_tasks:
        lines.append("⏳ <b>Pendentes/Perdidas:</b>")
        for t in pending_tasks:
            lines.append(f"  • {t}")
        lines.append("")

    # Suggestions
    lines.append("💡 <b>Sugestoes para amanha:</b>")
    if pending_tasks:
        lines.append(f"  • Reagendar {len(pending_tasks)} tarefa(s) pendente(s)")
    if events_total == 0 and not done_tasks:
        lines.append("  • Definir pelo menos 1 objetivo para o dia")
    else:
        lines.append("  • Continuar o bom ritmo!")

    summary_text = "\n".join(lines)

    # Save to Anytype
    if anytype_client:
        try:
            body = f"Eventos: {events_total}\nConcluidas: {len(done_tasks)}\nPendentes: {len(pending_tasks)}"
            if done_tasks:
                body += "\n\nConcluidas:\n" + "\n".join(f"- {t}" for t in done_tasks)
            if pending_tasks:
                body += "\n\nPendentes:\n" + "\n".join(f"- {t}" for t in pending_tasks)
            anytype_client.save_daily_recap(date_str, body)
            logger.info("Daily recap saved to Anytype")
        except Exception as e:
            logger.warning("Failed to save recap to Anytype: {}", e)

    return summary_text
