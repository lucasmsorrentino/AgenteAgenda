"""Parse natural-language `/ia` messages into structured actions.

The LLM returns JSON like:
    {
      "action": "create_appointment" | "create_task" | "create_note"
              | "update_event" | "cancel_event" | "unknown",
      "title": "...",
      "start": "2026-04-18T14:00:00-03:00",     // when applicable
      "end": "2026-04-18T15:00:00-03:00",       // optional
      "location": "...",                         // optional
      "recurrence": "FREQ=WEEKLY;BYDAY=MO",      // optional RRULE body
      "event_id_prefix": "abc123",               // for update/cancel
      "fields": {"titulo": "...", ...},          // for update
      "tags": ["estudo"], "area": "tcc", "prioridade": "media",
      "reply": "Compromisso criado: ..."         // short human-readable
    }

Unknown actions are returned verbatim so the bot can show the `reply` to the
user without acting on anything.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger

from config.labels import taxonomy_prompt_block
from config.settings import TIMEZONE
from services.ai_subprocess import AISubprocessError, run_claude


def _build_prompt(user_text: str, agenda_snippet: str) -> str:
    now = datetime.now(ZoneInfo(TIMEZONE))
    now_str = now.strftime("%Y-%m-%d %H:%M %A")
    return f"""You are the parser for a Portuguese productivity bot. Convert the user's message into a single JSON action. Return ONLY JSON, no prose, no code fences.

Current time: {now_str} (timezone: {TIMEZONE})

Upcoming agenda (for resolving references like "a reuniao de amanha"):
{agenda_snippet or "(empty)"}

Taxonomy for classification (always include tags/area/prioridade):
{taxonomy_prompt_block()}

Allowed actions:
- create_appointment: real calendar event. Required: title, start (ISO8601 with tz offset). Optional: end, location, recurrence (RRULE body without prefix).
- create_task: TODO with deadline. Required: title. Optional: start (deadline).
- create_note: quick note. Required: title. Optional: body.
- update_event: edit an existing event. Required: event_id_prefix (6-char), fields ({{titulo?, inicio?, fim?, local?}}).
- cancel_event: Required: event_id_prefix.
- unknown: if you can't parse. Put the explanation in `reply`.

Always include tags (list), area (string), prioridade ("alta"|"media"|"baixa"), and a short `reply` in pt-BR.

User message: {user_text}

Respond with JSON only."""


async def parse_ia_message(user_text: str, agenda_snippet: str = "") -> dict:
    """Parse a /ia message via Claude subprocess. Returns the action dict.

    Raises AISubprocessError on subprocess/JSON failures — caller should catch
    and fall back to a friendly error.
    """
    prompt = _build_prompt(user_text, agenda_snippet)
    result = await run_claude(prompt)
    if not isinstance(result, dict):
        raise AISubprocessError(f"Expected object, got {type(result).__name__}")
    if "action" not in result:
        logger.warning("/ia response missing 'action': {}", result)
        result["action"] = "unknown"
    return result
