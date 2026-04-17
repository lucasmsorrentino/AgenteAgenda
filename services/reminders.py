"""Reminder service.

Checks for upcoming events and sends Telegram notifications.
Designed to run periodically via APScheduler inside the bot process.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger

from config.settings import REMINDER_MINUTES_BEFORE, TIMEZONE

# Track which events already had reminders sent (in-memory, resets on restart)
_sent_reminders: set[str] = set()


async def check_and_send_reminders(
    calendar_client,
    bot,
    reminder_minutes: int = REMINDER_MINUTES_BEFORE,
) -> None:
    """Check for upcoming events and send reminders via the bot.

    Called periodically by APScheduler.

    Args:
        calendar_client: GoogleCalendarClient instance
        bot: ProductivityBot instance (must be running)
        reminder_minutes: how many minutes before an event to send reminder
    """
    if not calendar_client:
        return

    try:
        upcoming = calendar_client.get_upcoming_events(minutes=reminder_minutes + 5)
    except Exception as e:
        logger.error("Reminder check failed: {}", e)
        return

    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)

    for event in upcoming:
        # Skip if already reminded
        if event.id in _sent_reminders:
            continue

        # Calculate minutes until event
        delta = (event.start - now).total_seconds() / 60

        # Send reminder if event is within the reminder window
        if 0 < delta <= reminder_minutes:
            minutes_until = int(delta)
            logger.info("Sending reminder for '{}' (in {} min)", event.title, minutes_until)

            try:
                await bot.send_reminder(
                    task_id=event.id,
                    title=event.title,
                    minutes_until=minutes_until,
                )
                _sent_reminders.add(event.id)
            except Exception as e:
                logger.error("Failed to send reminder for '{}': {}", event.title, e)

    # Clean old reminders (keep set from growing indefinitely)
    if len(_sent_reminders) > 500:
        _sent_reminders.clear()
        logger.debug("Cleared reminder cache")
