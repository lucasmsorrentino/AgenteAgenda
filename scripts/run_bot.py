"""Main entry point — starts the Telegram bot with APScheduler for reminders.

Usage:
    cd productivity
    python scripts/run_bot.py

The bot runs in long-polling mode (no public IP needed).
APScheduler checks for upcoming events every 10 minutes and sends reminders.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add project root to path so we can import our modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from config.settings import ANYTYPE_API_KEY, ANYTYPE_SPACE_ID, TIMEZONE
from integrations.anytype_client import AnytypeClient
from integrations.google_calendar import GoogleCalendarClient
from integrations.telegram_bot import ProductivityBot
from services.calendar_sync import sync_calendar_to_anytype
from services.reminders import check_and_send_reminders


async def main():
    """Initialize all integrations and start the bot."""
    logger.info("Starting Productivity Bot...")

    # --- Google Calendar ---
    calendar = None
    try:
        calendar = GoogleCalendarClient()
        calendar.authenticate()
        logger.info("Google Calendar: OK")
    except FileNotFoundError as e:
        logger.warning("Google Calendar: {} — running without calendar", e)
        calendar = None
    except Exception as e:
        logger.warning("Google Calendar auth failed: {} — running without calendar", e)
        calendar = None

    # --- Anytype ---
    anytype = None
    if ANYTYPE_API_KEY and ANYTYPE_SPACE_ID:
        anytype = AnytypeClient()
        if anytype.verify_connection():
            logger.info("Anytype: OK")
        else:
            logger.warning("Anytype: offline — running without Anytype")
            anytype = None
    else:
        logger.info("Anytype: not configured — skipping")

    # --- Telegram Bot ---
    bot = ProductivityBot(
        calendar_client=calendar,
        anytype_client=anytype,
    )

    # --- APScheduler for reminders (every 10 min) ---
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)

    if calendar:
        scheduler.add_job(
            lambda: asyncio.create_task(check_and_send_reminders(calendar, bot)),
            trigger=IntervalTrigger(minutes=10),
            id="reminder_check",
            name="Check upcoming events",
            misfire_grace_time=300,
        )
        logger.info("Reminder check scheduled every 10 minutes")

    if calendar and anytype:
        scheduler.add_job(
            lambda: asyncio.create_task(sync_calendar_to_anytype(calendar, anytype)),
            trigger=IntervalTrigger(hours=6),
            id="calendar_sync",
            name="Sync Calendar to Anytype",
            misfire_grace_time=3600,
        )
        logger.info("Calendar sync scheduled every 6 hours")

    scheduler.start()

    # --- Run the bot ---
    try:
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        scheduler.shutdown(wait=False)
        await bot.stop()
        if anytype:
            anytype.close()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot encerrado.")
