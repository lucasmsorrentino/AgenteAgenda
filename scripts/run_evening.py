"""Standalone script to send the evening recap via Telegram.

Designed to be called by Claude Scheduled Tasks or manually:
    cd productivity
    python scripts/run_evening.py
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
from services.evening_recap import generate_evening_recap


async def main():
    logger.info("Generating evening recap...")

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
    text = await generate_evening_recap(calendar, anytype)
    await send_telegram_message(text)
    logger.info("Evening recap sent!")


if __name__ == "__main__":
    asyncio.run(main())
