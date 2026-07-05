"""Email scheduler service.

Reads email triagem JSON files and suggests calendar events for important items.
Items with grau A or B are candidates for scheduling.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

from config.settings import TIMEZONE

TRIAGEM_DIR = Path.home() / "ai_os" / "signals" / "email-pessoal"


def _load_latest_triagem() -> dict | None:
    """Load the most recent triagem-*.json file."""
    if not TRIAGEM_DIR.exists():
        return None
    
    triagem_files = sorted(TRIAGEM_DIR.glob("triagem-*.json"), reverse=True)
    if not triagem_files:
        return None
    
    try:
        return json.loads(triagem_files[0].read_text())
    except Exception as e:
        logger.error("Failed to load triagem file {}: {}", triagem_files[0], e)
        return None


def _suggest_date(event: dict, now: datetime) -> datetime | None:
    """Suggest a date for scheduling an email item.
    
    grau A (urgent): today or tomorrow
    grau B (normal): within 3 days
    grau C (low): not scheduled
    
    Returns None if item shouldn't be scheduled.
    """
    grau = event.get("grau", "C")
    
    if grau == "A":
        # Urgent: suggest today at 14:00 or tomorrow at 10:00
        today_14 = now.replace(hour=14, minute=0, second=0, microsecond=0)
        if now < today_14:
            return today_14
        else:
            return (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    
    elif grau == "B":
        # Normal: suggest tomorrow at 14:00 or day after at 10:00
        tomorrow_14 = (now + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
        return tomorrow_14
    
    else:
        # Low priority: don't schedule
        return None


def get_schedulable_items() -> list[dict]:
    """Get email items that can be scheduled.
    
    Returns list of dicts with:
    - id: email message ID
    - de: sender
    - assunto: subject
    - resumo: summary
    - grau: priority (A/B/C)
    - data_sugerida: suggested datetime (ISO format) or None
    """
    triagem = _load_latest_triagem()
    if not triagem:
        return []
    
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    
    items = triagem.get("itens", [])
    schedulable = []
    
    for item in items:
        grau = item.get("grau", "C")
        if grau not in ("A", "B"):
            continue
        
        suggested = _suggest_date(item, now)
        
        schedulable.append({
            "id": item.get("id", ""),
            "de": item.get("de", ""),
            "assunto": item.get("assunto", ""),
            "resumo": item.get("resumo", ""),
            "grau": grau,
            "data_sugerida": suggested.isoformat() if suggested else None,
        })
    
    logger.info("Found {} schedulable email items (grau A/B)", len(schedulable))
    return schedulable


async def create_event_from_email(
    calendar_client,
    item: dict,
    duration_minutes: int = 30,
) -> str | None:
    """Create a calendar event from an email item.
    
    Args:
        calendar_client: GoogleCalendarClient instance
        item: dict from get_schedulable_items()
        duration_minutes: event duration in minutes
    
    Returns:
        Event ID if successful, None otherwise
    """
    if not calendar_client:
        logger.warning("Calendar client not available")
        return None
    
    data_sugerida = item.get("data_sugerida")
    if not data_sugerida:
        logger.warning("No suggested date for item: {}", item.get("assunto"))
        return None
    
    try:
        start_dt = datetime.fromisoformat(data_sugerida)
    except ValueError as e:
        logger.error("Invalid date format: {}", data_sugerida)
        return None
    
    end_dt = start_dt + timedelta(minutes=duration_minutes)
    
    # Build event title with priority indicator
    grau = item.get("grau", "B")
    priority_marker = "🔴" if grau == "A" else "🟡"
    title = f"{priority_marker} {item.get('assunto', 'Email')}"
    
    # Build description
    description = f"De: {item.get('de', 'Desconhecido')}\n\n{item.get('resumo', '')}"
    
    try:
        event_id = calendar_client.create_event(
            title=title,
            start=start_dt,
            end=end_dt,
            description=description,
        )
        logger.info("Created calendar event '{}' at {}", title, start_dt)
        return event_id
    except Exception as e:
        logger.error("Failed to create event from email: {}", e)
        return None
