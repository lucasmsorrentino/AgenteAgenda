"""Main entry point — starts the Telegram bot with APScheduler for reminders.

Usage:
    cd AgenteAgenda
    python scripts/run_bot.py

The bot runs in long-polling mode (no public IP needed).
APScheduler checks for upcoming events every 10 minutes and sends reminders.

Reliability features:
- Linux file lock (fcntl) prevents multiple instances (avoids Telegram getUpdates conflicts)
- Auto-restart with exponential backoff on crash (up to 5 min cap)
- Graceful shutdown on SIGINT/SIGTERM
- All integrations optional (degrades gracefully)
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import signal
import sys
import time
from pathlib import Path

# Add project root to path so we can import our modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from config.settings import TIMEZONE

# --- Linux file lock to prevent multiple instances ---

LOCK_FILE = Path(__file__).resolve().parent.parent / "data" / "bot.lock"
_lock_fh = None  # keep file handle open for the lock's lifetime


def _acquire_lock() -> bool:
    """Acquire an exclusive file lock. Returns True if successful.

    Uses fcntl.flock() on Linux. The lock is held as long as the file
    handle stays open. If another process holds the lock, this returns
    False immediately.
    """
    global _lock_fh
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)

    try:
        _lock_fh = open(LOCK_FILE, "w")
        fcntl.flock(_lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fh.write(str(os.getpid()))
        _lock_fh.flush()
        logger.info("Lock acquired (PID {})", os.getpid())
        return True
    except (IOError, OSError):
        logger.error(
            "Another bot instance is already running. "
            "Kill it first or delete data/bot.lock if stale."
        )
        if _lock_fh:
            _lock_fh.close()
            _lock_fh = None
        return False


def _release_lock() -> None:
    """Release the file lock and clean up."""
    global _lock_fh
    if _lock_fh:
        try:
            fcntl.flock(_lock_fh.fileno(), fcntl.LOCK_UN)
            _lock_fh.close()
        except Exception:
            pass
        _lock_fh = None
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
    except Exception:
        pass
    logger.info("Lock released")


# --- Main bot setup ---


async def _start_bot() -> None:
    """Initialize all integrations and run the bot. Raises on fatal error."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    from integrations.google_calendar import GoogleCalendarClient
    from integrations.knowledge import get_knowledge_client
    from integrations.telegram_bot import ProductivityBot
    from services.calendar_sync import sync_calendar_to_anytype
    from services.reminders import check_and_send_reminders

    logger.info("Starting Productivity Bot (PID {})...", os.getpid())

    # --- Google Calendar ---
    calendar = None
    try:
        calendar = GoogleCalendarClient()
        calendar.authenticate()
        logger.info("Google Calendar: OK")
    except FileNotFoundError as e:
        logger.warning("Google Calendar: {} — running without calendar", e)
    except Exception as e:
        logger.warning(
            "Google Calendar auth failed: {} — running without calendar", e
        )

    # --- Knowledge store (Obsidian by default; see integrations/knowledge.py) ---
    knowledge = get_knowledge_client()
    if knowledge is None:
        logger.info("Knowledge store: not configured — skipping")

    # --- Telegram Bot ---
    bot = ProductivityBot(
        calendar_client=calendar,
        anytype_client=knowledge,
    )

    # --- APScheduler for reminders (every 10 min) ---
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)

    if calendar:
        scheduler.add_job(
            check_and_send_reminders,
            args=[calendar, bot],
            trigger=IntervalTrigger(minutes=10),
            id="reminder_check",
            name="Check upcoming events",
            misfire_grace_time=300,
        )
        logger.info("Reminder check scheduled every 10 minutes")

    if calendar and knowledge:
        scheduler.add_job(
            sync_calendar_to_anytype,
            args=[calendar, knowledge],
            trigger=IntervalTrigger(hours=6),
            id="calendar_sync",
            name="Sync Calendar to knowledge store",
            misfire_grace_time=3600,
        )
        logger.info("Calendar sync scheduled every 6 hours ({})", type(knowledge).__name__)

    scheduler.start()

    # --- Run the bot ---
    try:
        await bot.run()
    finally:
        scheduler.shutdown(wait=False)
        await bot.stop()
        if knowledge:
            knowledge.close()
        logger.info("Bot stopped.")


# --- Auto-restart loop ---

MAX_BACKOFF = 300  # 5 minutes cap
INITIAL_BACKOFF = 5  # start at 5 seconds
HEALTHY_THRESHOLD = 120  # if bot ran for 2+ min, reset backoff


def main() -> None:
    """Run the bot with auto-restart on crash."""
    if not _acquire_lock():
        sys.exit(1)

    # Handle SIGTERM (from Task Scheduler stop / kill) gracefully
    def _handle_signal(sig, frame):
        logger.info("Received signal {}, shutting down...", sig)
        _release_lock()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    backoff = INITIAL_BACKOFF
    restart_count = 0

    try:
        while True:
            start_time = time.monotonic()
            restart_count += 1

            try:
                logger.info("=== Bot starting (attempt #{}) ===", restart_count)
                asyncio.run(_start_bot())
                # Clean exit (e.g. Ctrl+C handled inside) — stop
                logger.info("Bot exited cleanly.")
                break

            except KeyboardInterrupt:
                logger.info("Interrupted by user.")
                break

            except SystemExit:
                break

            except Exception as e:
                elapsed = time.monotonic() - start_time
                logger.error("Bot crashed after {:.0f}s: {}", elapsed, e)

                # If it ran for a while, the crash is likely transient — reset backoff
                if elapsed >= HEALTHY_THRESHOLD:
                    backoff = INITIAL_BACKOFF
                    logger.info(
                        "Was healthy for {}s, resetting backoff", int(elapsed)
                    )
                else:
                    backoff = min(backoff * 2, MAX_BACKOFF)

                logger.info("Restarting in {}s...", backoff)
                time.sleep(backoff)
    finally:
        _release_lock()


if __name__ == "__main__":
    main()
