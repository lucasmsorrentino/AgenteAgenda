"""Standalone script to send the morning summary via Telegram.

Designed to be called by Claude Scheduled Tasks or manually:
    cd productivity
    python scripts/run_morning.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from config.settings import ANYTYPE_API_KEY, ANYTYPE_SPACE_ID
from integrations.anytype_client import AnytypeClient
from integrations.google_calendar import GoogleCalendarClient
from integrations.telegram_bot import send_telegram_message
from services.morning_summary import generate_morning_summary


async def main():
    logger.info("Generating morning summary...")

    # Google Calendar
    calendar = None
    try:
        calendar = GoogleCalendarClient()
        calendar.authenticate()
    except Exception as e:
        logger.warning("Calendar unavailable: {}", e)

    # Anytype
    anytype = None
    if ANYTYPE_API_KEY and ANYTYPE_SPACE_ID:
        anytype = AnytypeClient()
        if not anytype.verify_connection():
            anytype = None

    # Generate and send
    text = await generate_morning_summary(calendar, anytype)
    await send_telegram_message(text)
    logger.info("Morning summary sent!")


if __name__ == "__main__":
    asyncio.run(main())
