"""Reminder service.

Checks for upcoming events and sends Telegram notifications.
Reminder lead time depends on event priority so high-priority items
surface earlier.

Designed to run periodically via APScheduler inside the bot process.
"""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger

from config.settings import REMINDER_MINUTES_BEFORE, TIMEZONE

# Track which events already had reminders sent (in-memory, resets on restart)
_sent_reminders: set[str] = set()

# Priority windows (minutes before event start)
PRIORITY_WINDOWS = {
    "A": int(os.getenv("REMINDER_MINUTES_A", "60")),
    "B": int(os.getenv("REMINDER_MINUTES_B", "30")),
    "C": REMINDER_MINUTES_BEFORE,
}


def _extract_priority(event) -> str:
    """Infer priority from event title markers.

    Priority markers come mainly from email-scheduled events:
      🔴 = high/urgent (A)
      🟡 = medium (B)

    Calendar [TODO] tasks are treated as medium (B) by default.
    Everything else is normal (C) and uses the default reminder window.
    """
    title = getattr(event, "title", "") or ""
    if "🔴" in title:
        return "A"
    if "🟡" in title:
        return "B"
    if getattr(event, "is_todo", False):
        return "B"
    return "C"


async def check_and_send_reminders(
    calendar_client,
    bot,
    reminder_minutes: int | None = None,
) -> None:
    """Check for upcoming events and send reminders via the bot.

    Called periodically by APScheduler.

    Args:
        calendar_client: GoogleCalendarClient instance
        bot: ProductivityBot instance (must be running)
        reminder_minutes: optional override for normal-priority (C) events
    """
    if not calendar_client:
        return

    try:
        # Look ahead far enough to catch high-priority items first.
        max_window = max(PRIORITY_WINDOWS.values())
        upcoming = calendar_client.get_upcoming_events(minutes=max_window + 5)
    except Exception as e:
        logger.error("Reminder check failed: {}", e)
        return

    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)

    for event in upcoming:
        # Skip if already reminded
        if event.id in _sent_reminders:
            continue

        priority = _extract_priority(event)
        reminder_min = reminder_minutes if reminder_minutes is not None else PRIORITY_WINDOWS[priority]
        delta = (event.start - now).total_seconds() / 60

        # Send reminder if event is within its priority window
        if 0 < delta <= reminder_min:
            minutes_until = int(delta)
            logger.info(
                "Sending reminder for '{}' (in {} min, priority {})",
                event.title,
                minutes_until,
                priority,
            )

            try:
                await bot.send_reminder(
                    task_id=event.id,
                    title=event.title,
                    minutes_until=minutes_until,
                    priority=priority,
                )
                _sent_reminders.add(event.id)
            except Exception as e:
                logger.error("Failed to send reminder for '{}': {}", event.title, e)

    # Clean old reminders (keep set from growing indefinitely)
    if len(_sent_reminders) > 500:
        _sent_reminders.clear()
        logger.debug("Cleared reminder cache")
