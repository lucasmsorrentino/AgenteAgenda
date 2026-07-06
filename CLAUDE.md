# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Personal productivity system: Telegram bot + Google Calendar + a local knowledge store. Python, asyncio-based, long-polling Telegram bot with APScheduler for periodic jobs. No web server; runs as a single local process.

The knowledge store is pluggable (see `integrations/knowledge.py`):
- **Obsidian (default)** — plain markdown vault, no daemon. Agenda notes are written under a single vault section (`OBSIDIAN_AGENDA_SUBDIR`, default `agenda/`): `agenda/tarefas`, `agenda/compromissos`, `agenda/notas`, `agenda/recaps`.
- **Anytype (legacy/optional)** — only used when `KNOWLEDGE_BACKEND=anytype` and its local REST API is running. Kept for backward compatibility; the default deployment uses Obsidian.

Deployment: the bot runs on the Linux notebook as a `systemd --user` service (`agenteagenda.service`), always-on via linger. The old Windows Startup launcher (`ProductivityBot.vbs` → `start_bot.ps1`) is legacy and disabled — do not run a second instance anywhere, since two pollers on the same bot token fight over `getUpdates` (HTTP 409 Conflict).

## Commands

All scripts must be run from the project root so `sys.path.insert(..., parent)` resolves imports correctly. Use the venv (`.venv`).

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# One-time setup (interactive OAuth flow)
.venv/bin/python scripts/setup_google.py    # Google Calendar OAuth2 → token.json
# scripts/setup_anytype.py                   # LEGACY: only if KNOWLEDGE_BACKEND=anytype

# Main bot (long-polling + scheduled reminders + calendar sync)
.venv/bin/python scripts/run_bot.py

# Standalone jobs (on-demand only, no scheduler)
.venv/bin/python scripts/run_morning.py      # Morning summary → Telegram
.venv/bin/python scripts/run_evening.py      # Evening recap → Telegram
.venv/bin/python scripts/run_classify.py     # Classifies unclassified store items via `claude -p`

# Tests
.venv/bin/python -m pytest -q

# LEGACY: weekly workout plan — still Anytype-only (setup_workout.py hardcodes AnytypeClient)
# .venv/bin/python scripts/setup_workout.py [--calendar] [--time HH:MM] [--force]
```

On the host, the bot is normally started via the service, not by hand:
`systemctl --user {status,restart,stop} agenteagenda.service` (secrets loaded by the host wrapper `~/agenda-secrets/run-agenteagenda.sh`, which lives outside this repo).

## Architecture

Integration clients composed by services, orchestrated by scripts:

- **`integrations/`** — thin clients, one per external system.
  - `google_calendar.py` — OAuth2, reads/writes events. Events with titles starting `[TODO]` / `[DONE]` are treated as tasks (see `models/schemas.py:Event.is_todo`). Generic event API: `create_event(...)`, `update_event(event_id, scope="single"|"all", ...)`, `delete_event(event_id, scope=...)`. `scope="all"` resolves the master via `recurringEventId` and edits/cancels the whole series.
  - `obsidian_client.py` — **default store.** Filesystem client over the Obsidian vault. Writes markdown notes with YAML frontmatter under `<vault>/<OBSIDIAN_AGENDA_SUBDIR>/{tarefas,compromissos,notas,recaps}`. Same method surface as the Anytype client (`create_task`, `create_appointment`, `update_appointment`, `set_classification`, `list_unclassified`, `search_objects`, …) so services are backend-agnostic. Object IDs are vault-relative paths (e.g. `agenda/compromissos/reuniao.md`).
  - `anytype_client.py` — **legacy.** httpx wrapper over the local Anytype REST API (`http://localhost:31009`). Only instantiated when `KNOWLEDGE_BACKEND=anytype`. Maps logical names (`tarefa`, `nota_rapida`, `compromisso`, …) to Anytype types; `compromisso` prefers the custom type from `data/anytype_schema.json` (created by `setup_anytype.py`), falling back to `page`.
  - `knowledge.py` — **backend factory.** `get_knowledge_client()` returns the store: Obsidian by default, Anytype only when `KNOWLEDGE_BACKEND=anytype` and its API is up (falls back to Obsidian otherwise). All entry points use this instead of instantiating a client directly.
  - `telegram_bot.py` — `ProductivityBot` wraps `python-telegram-bot` Application. Owner-allowlist via `TELEGRAM_CHAT_ID` + `TELEGRAM_ALLOWED_IDS`. Inline keyboards route through `CallbackQueryHandler`. Also exposes `send_telegram_message()` for one-shot scripts. (The `anytype` attribute/params hold whatever knowledge client the factory returned — the name is historical.)

- **`services/`** — business logic, no direct I/O beyond the passed-in clients.
  - `calendar_sync.py` — Calendar → knowledge-store mirror. Calendar is source of truth. State in `data/sync_state.json` maps each Google event ID to `{object_id, updated, start_iso, type}`. Each sync diffs by Google's `updated` timestamp: new → create, changed → patch, missing-inside-window → delete. Pass `only_event_ids={...}` for incremental sync (skips delete detection). `_load_sync_state` migrates the legacy per-entry field `anytype_id` → `object_id` on read.
  - `recurrence.py` — parses Portuguese phrases ("toda seg, qua", "mensal dia 15") into RFC 5545 RRULE strings.
  - `ai_subprocess.py` — invokes `claude -p <prompt> --output-format json` as a subprocess. Uses the user's Claude Code subscription quota (no API key). `run_claude(prompt, timeout=90)` is the only entry point.
  - `ai_parser.py` — parses `/ia` free-text into a structured action plus taxonomy tags, grounded with a short agenda snippet.
  - `ai_classifier.py` — batch classifier. Reads store items with empty `classified_at`, one LLM call per batch, writes back `area`/`prioridade`/`tags`/`classified_at`. `clamp_to_taxonomy()` sanitizes LLM output against `config/labels.py`.
  - `ai_search.py` — `/buscar` handler over recent store items + calendar window.
  - `agenda_writer.py` — writes a formatted weekly agenda to `~/ai_os/signals/agenda.md` (consumed by the Hermes digest).
  - `morning_summary.py` / `evening_recap.py` — compose daily messages.
  - `reminders.py` — `check_and_send_reminders()`, invoked every 10 min by APScheduler. Lead time varies by priority (`REMINDER_MINUTES_A/B`, else `REMINDER_MINUTES_BEFORE`).
  - `task_manager.py` — task state transitions.

- **`config/labels.py`** — closed taxonomy (`AREAS`, `PRIORIDADES`, `TAGS`) used by the AI classifier and `/ia`.

- **`scripts/run_bot.py`** is the orchestrator: authenticates Calendar, resolves the knowledge store via `get_knowledge_client()`, constructs `ProductivityBot`, and attaches two APScheduler jobs (reminders every 10 min, calendar sync every 6 h). Each integration is optional — if auth fails, the bot degrades gracefully. Edits via `/novo`, `/editar`, `/cancelar` also trigger an incremental sync of just the touched event IDs.

- **`scripts/start_bot.ps1` / `ProductivityBot.vbs`** — **legacy Windows launchers**, superseded by the Linux systemd service. Left in the repo for reference; not part of the live deployment.

## Telegram commands for appointments

- `/add Texto @prazo` — creates a `[TODO]` event + store task. Tasks only.
- `/novo Texto @quando [ate HH:MM] [em local] [repete <regra>]` — creates a real calendar event. `repete` accepts pt-BR phrases parsed by `services/recurrence.py`. Without `repete`, a single-occurrence event.
- `/agenda [dias]` — lists upcoming events with a 6-char prefix ID used by `/editar` and `/cancelar`. Recurring instances marked with 🔁.
- `/editar <id> campo=valor [...]` — fields: `titulo`, `inicio` (DD/MM HH:MM), `fim` (HH:MM), `local`. For recurring events, prompts scope via inline buttons. Pending ops in `ProductivityBot._pending_ops`.
- `/cancelar <id>` — same scope prompt for recurring events.

## AI-powered commands

All use `services/ai_subprocess.run_claude()` — no API key, uses the local `claude` CLI + the user's subscription quota. Calls are stateless.

- `/ia <texto livre>` — one-shot parser. Creates appointments/tasks/notes or edits/cancels events in natural language; returns taxonomy labels in the same call.
- `/classificar` — batch-classifies every store item with empty `classified_at`. On-demand only.
- `/buscar <pergunta>` — natural-language search over store items + calendar window (last 7d, next 60d). Returns pt-BR answer + 6-char id prefixes.

The `classified_at` property is the single source of truth for "already classified". `/ia`, `/classificar`, and calendar sync all set it; `list_unclassified()` uses its absence to build the batch queue.

- **`models/schemas.py`** — Pydantic models (`Event`, `Task`, `DailyRecap`, `ReminderAction`) shared across services.
- **`config/settings.py`** — single source of env vars; loads `.env` from project root with `override=False`, so env pre-set by the host wrapper wins. Key vars: `KNOWLEDGE_BACKEND`, `OBSIDIAN_VAULT_PATH`, `OBSIDIAN_AGENDA_SUBDIR`, `GOOGLE_*`, `TELEGRAM_*`. All modules import from here rather than calling `os.getenv` directly.

## Bot reliability

`run_bot.py` keeps the bot alive:

- **Linux file lock (`fcntl`)** — a lock on `data/bot.lock` prevents multiple instances from polling Telegram simultaneously (HTTP 409 Conflict). A second `run_bot.py` exits immediately. Note: a Conflict can also come from a stale instance on *another* machine sharing the same bot token (see Overview).
- **Auto-restart with exponential backoff** — on unhandled crash, sleeps (5s → … → 5min cap) then restarts; backoff resets after 2+ min healthy.
- **Graceful shutdown** — SIGINT/SIGTERM release the lock and exit cleanly (systemd sends SIGTERM on stop/restart).
- **Always-on** — via `systemd --user agenteagenda.service` (enable + linger). `journalctl --user -u agenteagenda.service` for logs.

## Conventions

- `from __future__ import annotations` at top of every module; PEP 604 unions (`str | None`).
- Timezone-aware datetimes via `zoneinfo.ZoneInfo(TIMEZONE)` — never naive `datetime.now()` for scheduling.
- Logging via `loguru`, brace-style: `logger.info("x={}", val)`.
- User-facing Telegram text is Portuguese (pt-BR); code/comments/logs are English.
- Tests: `pytest` + `pytest-asyncio` under `tests/`.
- `data/` holds runtime artifacts (`credentials.json`, `token.json`, `sync_state.json`, and the legacy `anytype_schema.json`) — gitignored, not code. Live secrets are provided by the host wrapper, not stored in this repo.
