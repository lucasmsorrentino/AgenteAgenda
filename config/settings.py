"""Centralized settings loaded from .env file.

Follows the same pattern as ufpr_automation/config/settings.py.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Project root is the parent of config/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# --- Telegram ---
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
# Comma-separated list of allowed chat IDs (owner is always allowed)
TELEGRAM_ALLOWED_IDS: str = os.getenv("TELEGRAM_ALLOWED_IDS", "")

# --- Google Calendar ---
GOOGLE_CREDENTIALS_PATH: str = os.getenv(
    "GOOGLE_CREDENTIALS_PATH",
    str(PROJECT_ROOT / "data" / "credentials.json"),
)
GOOGLE_TOKEN_PATH: str = os.getenv(
    "GOOGLE_TOKEN_PATH",
    str(PROJECT_ROOT / "data" / "token.json"),
)
GOOGLE_CALENDAR_ID: str = os.getenv("GOOGLE_CALENDAR_ID", "primary")

# --- Anytype ---
ANYTYPE_BASE_URL: str = os.getenv("ANYTYPE_BASE_URL", "http://localhost:31009")
ANYTYPE_API_KEY: str = os.getenv("ANYTYPE_API_KEY", "")
ANYTYPE_API_VERSION: str = os.getenv("ANYTYPE_API_VERSION", "2025-11-08")
ANYTYPE_SPACE_ID: str = os.getenv("ANYTYPE_SPACE_ID", "")

# --- Knowledge backend selector ---
# "obsidian" (default) or "anytype". Obsidian needs no running daemon.
KNOWLEDGE_BACKEND: str = os.getenv("KNOWLEDGE_BACKEND", "obsidian")

# --- Obsidian (filesystem vault, replacement for Anytype) ---
OBSIDIAN_VAULT_PATH: str = os.getenv(
    "OBSIDIAN_VAULT_PATH",
    str(Path.home() / "ai_os"),
)
# All agenda notes live under this subfolder of the vault (its own "section",
# like the Anytype space): agenda/tarefas, agenda/compromissos, agenda/notas,
# agenda/recaps.
OBSIDIAN_AGENDA_SUBDIR: str = os.getenv("OBSIDIAN_AGENDA_SUBDIR", "agenda")

# --- General ---
TIMEZONE: str = os.getenv("TIMEZONE", "America/Sao_Paulo")
REMINDER_MINUTES_BEFORE: int = int(os.getenv("REMINDER_MINUTES_BEFORE", "15"))
