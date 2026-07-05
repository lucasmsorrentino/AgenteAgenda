"""Agenda writer service.

Reads Google Calendar events and writes a formatted agenda to ~/ai_os/signals/agenda.md.
This file is consumed by the Hermes digest cron job.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

from config.settings import TIMEZONE

AGENDA_PATH = Path.home() / "ai_os" / "signals" / "agenda.md"


def _format_event(event) -> str:
    """Format a single calendar event as markdown."""
    parts = []
    
    # Time
    time_str = event.start.strftime("%H:%M")
    if event.end and event.end != event.start:
        time_str += f" - {event.end.strftime('%H:%M')}"
    parts.append(f"**{time_str}**")
    
    # Title
    parts.append(event.title)
    
    # Location
    if event.location:
        parts.append(f"📍 {event.location}")
    
    # Recurring indicator
    if event.recurring_event_id:
        parts.append("🔁")
    
    return " ".join(parts)


async def write_agenda(
    calendar_client,
    days_forward: int = 7,
    output_path: Path | None = None,
) -> bool:
    """Write upcoming calendar events to agenda.md.
    
    Args:
        calendar_client: GoogleCalendarClient instance
        days_forward: how many days ahead to include
        output_path: where to write the file (defaults to ~/ai_os/signals/agenda.md)
    
    Returns:
        True if successful, False otherwise
    """
    if not calendar_client:
        logger.warning("Calendar client not available for agenda writer")
        return False
    
    output_path = output_path or AGENDA_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    
    try:
        events = calendar_client.get_all_events_range(0, days_forward)
    except Exception as e:
        logger.error("Failed to fetch calendar events for agenda: {}", e)
        return False
    
    # Group events by date
    events_by_date: dict[str, list] = {}
    for event in events:
        date_key = event.start.strftime("%Y-%m-%d")
        if date_key not in events_by_date:
            events_by_date[date_key] = []
        events_by_date[date_key].append(event)
    
    # Sort events within each day by start time
    for date_key in events_by_date:
        events_by_date[date_key].sort(key=lambda e: e.start)
    
    # Build markdown content
    lines = []
    lines.append("# Agenda da Semana")
    lines.append("")
    lines.append(f"_Gerado em {now.strftime('%d/%m/%Y às %H:%M')}_")
    lines.append("")
    
    if not events_by_date:
        lines.append("Nenhum compromisso nos próximos {} dias.".format(days_forward))
    else:
        # Write events grouped by date
        for date_key in sorted(events_by_date.keys()):
            date_events = events_by_date[date_key]
            
            # Format date header
            date_dt = datetime.fromisoformat(date_key)
            weekday_pt = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
            weekday = weekday_pt[date_dt.weekday()]
            date_str = date_dt.strftime("%d/%m")
            
            lines.append(f"## {weekday} ({date_str})")
            lines.append("")
            
            for event in date_events:
                lines.append(f"- {_format_event(event)}")
            
            lines.append("")
    
    # Write to file
    try:
        output_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Agenda written to {} ({} events)", output_path, len(events))
        return True
    except Exception as e:
        logger.error("Failed to write agenda file: {}", e)
        return False
