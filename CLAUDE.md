# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Personal productivity system: Telegram bot + Google Calendar + Anytype (local REST API). Python, asyncio-based, long-polling Telegram bot with APScheduler for periodic jobs. No web server; runs as a single local process.

## Commands

All scripts must be run from the `productivity/` directory so `sys.path.insert(..., parent)` resolves imports correctly.

```bash
pip install -r requirements.txt

# One-time setup (interactive OAuth / API key flows)
python scripts/setup_google.py      # Google Calendar OAuth2 → data/token.json
python scripts/setup_anytype.py     # Anytype local API auth + schema → data/anytype_schema.json

# Main bot (long-polling + scheduled reminders + calendar sync)
python scripts/run_bot.py

# Standalone jobs (on-demand only, no scheduler)
python scripts/run_morning.py       # Morning summary → Telegram
python scripts/run_evening.py       # Evening recap → Telegram
python scripts/run_classify.py      # Classifies unclassified Anytype items via `claude -p`
```

No test suite or linter is configured.

## Architecture

Three integration clients, composed by services, orchestrated by scripts:

- **`integrations/`** — thin clients, one per external system.
  - `google_calendar.py` — OAuth2, reads/writes events. Events with titles starting `[TODO]` / `[DONE]` are treated as tasks (see `models/schemas.py:Event.is_todo`). Generic event API: `create_event(title, start, end, ..., recurrence=["RRULE:..."])`, `update_event(event_id, scope="single"|"all", **fields)`, `delete_event(event_id, scope=...)`. `scope="all"` resolves the master via `recurringEventId` and edits/cancels the whole series.
  - `anytype_client.py` — httpx wrapper over local Anytype REST API (`http://localhost:31009`). `TYPE_MAP` maps logical names (`tarefa`, `nota_rapida`, `resumo_diario`, `compromisso`) to Anytype built-in types (`task`, `note`, `page`). `compromisso` resolves at import time: prefers the custom `Compromisso` type from `data/anytype_schema.json` (created by `setup_anytype.py`) and falls back to `page` if the schema isn't present yet. `create_appointment` / `update_appointment` set the structured properties (`start`, `end`, `location`, `calendar_event_id`, `recurring`) when those keys exist in the schema — used by `calendar_sync.py` so events show up in Anytype's calendar view.
  - `telegram_bot.py` — `ProductivityBot` class wraps `python-telegram-bot` Application. Owner-allowlist enforced via `TELEGRAM_CHAT_ID` + `TELEGRAM_ALLOWED_IDS`. Inline keyboards (Feito/Pular/Adiar) route through `CallbackQueryHandler`. Module also exposes `send_telegram_message()` for one-shot scripts.

- **`services/`** — business logic, no direct I/O with external APIs beyond the passed-in clients.
  - `calendar_sync.py` — Calendar → Anytype mirror. Calendar is source of truth. State in `data/sync_state.json` maps each Google event ID to `{anytype_id, updated, start_iso, type}`. Each sync diffs by Google's `updated` timestamp: new events → create, changed → patch, missing-inside-window → delete. Pass `only_event_ids={...}` for incremental sync (skips delete detection).
  - `recurrence.py` — parses Portuguese phrases ("toda seg, qua", "mensal dia 15", "diario ate 30/06") into RFC 5545 RRULE strings used by `GoogleCalendarClient.create_event`.
  - `ai_subprocess.py` — invokes `claude -p <prompt> --output-format json` as a subprocess, unwraps the envelope, extracts the JSON payload. Uses the user's Claude Code Max quota (no API key). `run_claude(prompt, timeout=90)` is the only entry point.
  - `ai_parser.py` — parses `/ia` free-text messages into a structured action (`create_appointment|create_task|create_note|update_event|cancel_event|unknown`) plus taxonomy tags. Grounded with a short agenda snippet so the LLM can resolve references like "a reuniao de amanha".
  - `ai_classifier.py` — batch classifier. Reads items with empty `classified_at`, sends them all in one LLM call, writes back `area`/`prioridade`/`tags`/`classified_at`. `clamp_to_taxonomy()` is the public helper both the batch flow and `/ia` use to sanitize LLM output against `config/labels.py`. Returns per-item `details` list with name/area/prioridade/tags for the detailed log shown via `/classificar` and CLI.
  - `ai_search.py` — `/buscar` handler. Fetches recent tasks/notes/compromissos + calendar window, asks Claude to answer in pt-BR, returns `{answer, cited_ids}`.
  - `morning_summary.py` / `evening_recap.py` — compose daily messages.
  - `reminders.py` — `check_and_send_reminders()` is invoked every 10 min by APScheduler from `run_bot.py`.
  - `task_manager.py` — task state transitions.

- **`config/labels.py`** — closed taxonomy (`AREAS`, `PRIORIDADES`, `TAGS`) used by the AI classifier and `/ia`. Both the prompt and the sanitizer read from here, so edits take effect immediately for new classifications.

- **`scripts/run_bot.py`** is the orchestrator: authenticates Calendar + Anytype, constructs `ProductivityBot`, and attaches two APScheduler jobs (reminders every 10 min, calendar sync every 6 h). Each integration is optional — if auth fails, the bot continues without it (degrades gracefully). Edits made via `/novo`, `/editar` and `/cancelar` also trigger an incremental sync of just the touched event IDs, so the 6 h schedule only handles drift from external Calendar edits.

- **`scripts/start_bot.ps1`** — PowerShell launcher for Windows Task Scheduler / Startup. Sets working directory, rotates logs (>5 MB), and delegates to `run_bot.py`. A VBS shim in `shell:startup` calls this script silently on login.

## Telegram commands for appointments

- `/add Texto @prazo` — creates a `[TODO]` event + Anytype task. Tasks only.
- `/novo Texto @quando [ate HH:MM] [em local] [repete <regra>]` — creates a real calendar event. `repete` accepts pt-BR phrases parsed by `services/recurrence.py` (`toda seg,qua,sex`, `dias uteis`, `mensal dia 15`, `semanal ate 30/06`, `diario 10 vezes`). Without `repete`, creates a single-occurrence event.
- `/agenda [dias]` — lists upcoming events with a 6-char prefix ID used by `/editar` and `/cancelar`. Recurring instances marked with 🔁.
- `/editar <id> campo=valor [...]` — fields: `titulo`, `inicio` (DD/MM HH:MM), `fim` (HH:MM), `local`. For recurring events, prompts scope (this instance vs whole series) via inline buttons. Pending ops live in `ProductivityBot._pending_ops`, keyed by 8-char token, consumed by `scope:single:<token>` / `scope:all:<token>` callbacks.
- `/cancelar <id>` — same scope prompt for recurring events.

## AI-powered commands

All three use `services/ai_subprocess.run_claude()` — no API key, uses the local `claude` CLI + the user's Max subscription quota. Calls are stateless (no session memory across invocations).

- `/ia <texto livre>` — one-shot parser. Creates appointments/tasks/notes or edits/cancels events in natural language. Always returns taxonomy labels in the same call, so items created through `/ia` are classified immediately (no extra subprocess).
- `/classificar` — batch-classifies every Anytype item with empty `classified_at`. On-demand only (no scheduler). One LLM call per batch of ~80 items; returns a detailed per-item log (name, area, prioridade, tags) plus summary counts.
- `/buscar <pergunta>` — natural-language search over Anytype items + calendar window (last 7d, next 60d). Returns an answer in pt-BR + 6-char id prefixes for cited items.

The `classified_at` date property is the single source of truth for "already classified". `/ia`, `/classificar`, and calendar sync all set it; `list_unclassified()` in `anytype_client.py` uses its absence to build the batch queue.

- **`models/schemas.py`** — Pydantic models (`Event`, `Task`, `DailyRecap`, `ReminderAction`) shared across services.

- **`config/settings.py`** — single source of env vars; loads `.env` from project root. All modules import from here rather than calling `os.getenv` directly.

## Bot reliability

`run_bot.py` includes several mechanisms to keep the bot alive:

- **Windows file lock (`msvcrt.locking`)** — prevents multiple instances from polling Telegram simultaneously (which causes HTTP 409 Conflict). The lock is held as long as the process is alive; a second `run_bot.py` exits immediately with an error.
- **Auto-restart with exponential backoff** — on unhandled crash, the process sleeps (5s → 10s → … → 5min cap) then restarts. If the bot was healthy for 2+ minutes before crashing, backoff resets to 5s (transient error).
- **Updater health check** — the polling loop checks every second if the Telegram updater is still running; if it died silently (e.g. conflict), it raises to trigger the restart loop.
- **Auto-start on login** — a VBS shim in `shell:startup` (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ProductivityBot.vbs`) launches `scripts/start_bot.ps1` silently. The PS1 script sets the working directory, rotates logs (>5 MB), and runs `python scripts/run_bot.py` with output piped to `data/bot_output.log`.
- **Graceful shutdown** — SIGINT and SIGTERM release the lock and exit cleanly.

Logs: `data/bot_stderr.log` (loguru output), `data/bot_output.log` (launcher wrapper).

## Conventions

- `from __future__ import annotations` at top of every module; PEP 604 union syntax (`str | None`).
- Timezone-aware datetimes everywhere via `zoneinfo.ZoneInfo(TIMEZONE)` — never use naive `datetime.now()` for scheduling logic.
- Logging via `loguru` (`from loguru import logger`). Brace-style: `logger.info("x={}", val)`.
- User-facing Telegram text is Portuguese (pt-BR); code/comments/logs are English.
- `data/` holds runtime artifacts (`credentials.json`, `token.json`, `sync_state.json`, `anytype_schema.json`) — gitignored, not code.
