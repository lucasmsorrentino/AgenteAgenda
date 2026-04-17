"""Natural-language search across Anytype items + Calendar events.

Fetches a recent+upcoming slice, formats it compactly, and asks Claude to
answer the question in pt-BR with a JSON envelope. The bot renders the
answer and lists any cited item IDs as a prefix for /editar or /cancelar.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger

from config.settings import TIMEZONE
from services.ai_subprocess import AISubprocessError, run_claude


def _format_property_value(prop: dict) -> str:
    """Pull the useful bit out of an Anytype property descriptor."""
    if "select" in prop and prop["select"]:
        return str(prop["select"].get("name", ""))
    if "multi_select" in prop and prop["multi_select"]:
        return ",".join(s.get("name", "") for s in prop["multi_select"])
    if "date" in prop and prop["date"]:
        return str(prop["date"])[:10]
    if "text" in prop:
        return str(prop["text"])[:60]
    if "checkbox" in prop:
        return "yes" if prop["checkbox"] else "no"
    return ""


def _format_anytype_item(obj: dict) -> str:
    oid = obj.get("id", "")[:10]
    name = (obj.get("name") or "").replace("\n", " ")[:100]
    snippet = (obj.get("snippet") or "").replace("\n", " ")[:100]
    props = obj.get("properties") or []
    area = ""
    tags: list[str] = []
    due = ""
    for p in props:
        k = p.get("key", "")
        if k in ("area", "Area"):
            area = _format_property_value(p)
        elif k in ("tags", "tag"):
            v = _format_property_value(p)
            if v:
                tags = v.split(",")
        elif k in ("due_date", "start"):
            if not due:
                due = _format_property_value(p)
    meta = []
    if area:
        meta.append(f"area={area}")
    if tags:
        meta.append(f"tags={','.join(tags)}")
    if due:
        meta.append(f"due={due}")
    return f"{oid} | {name}" + (f" | {'; '.join(meta)}" if meta else "") + (f" | {snippet}" if snippet else "")


def _format_event(ev) -> str:
    when = ev.start.strftime("%Y-%m-%d %H:%M")
    loc = f" @ {ev.location}" if ev.location else ""
    recur = " [recorrente]" if ev.recurring_event_id else ""
    return f"{ev.id[:10]} | {when} | {ev.title}{loc}{recur}"


def _gather_context(anytype_client, calendar_client) -> str:
    """Collect a compact context block for the LLM."""
    sections: list[str] = []

    if anytype_client:
        try:
            notes = anytype_client.list_objects(type_key="nota_rapida", limit=60) or []
            tasks = anytype_client.list_objects(type_key="tarefa", limit=80) or []
            apts = anytype_client.list_objects(type_key="compromisso", limit=80) or []
        except Exception as e:
            logger.warning("Anytype fetch failed during search: {}", e)
            notes, tasks, apts = [], [], []

        if tasks:
            sections.append("TASKS:\n" + "\n".join(_format_anytype_item(t) for t in tasks))
        if notes:
            sections.append("NOTES:\n" + "\n".join(_format_anytype_item(n) for n in notes))
        if apts:
            sections.append("COMPROMISSOS:\n" + "\n".join(_format_anytype_item(a) for a in apts))

    if calendar_client:
        try:
            events = calendar_client.get_all_events_range(days_back=7, days_forward=60)
            if events:
                sections.append("CALENDAR:\n" + "\n".join(_format_event(e) for e in events[:100]))
        except Exception as e:
            logger.warning("Calendar fetch failed during search: {}", e)

    return "\n\n".join(sections) if sections else "(no data)"


async def search(question: str, anytype_client=None, calendar_client=None) -> dict:
    """Answer a natural-language question over the user's data.

    Returns {"answer": str, "cited_ids": [str]}. On failure returns an
    `answer` describing the error and empty cited_ids.
    """
    context = _gather_context(anytype_client, calendar_client)
    now_str = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M %A")

    prompt = f"""You answer questions in Portuguese about the user's tasks, notes, appointments and calendar events. Today: {now_str}.

Context (id prefixes are truncated to 10 chars; each line is one item):
{context}

Question: {question}

Rules:
- Be concise but useful. Use pt-BR.
- Cite item ids you relied on in `cited_ids`.
- If the answer isn't in the context, say so plainly — don't invent items.
- Return ONLY JSON, no prose, no fences.

JSON shape:
{{"answer": "...", "cited_ids": ["abc1234567", ...]}}"""

    try:
        result = await run_claude(prompt, timeout=120.0)
    except AISubprocessError as e:
        logger.error("Search subprocess failed: {}", e)
        return {"answer": f"Erro ao consultar o modelo: {e}", "cited_ids": []}

    if not isinstance(result, dict):
        return {"answer": "Resposta invalida do modelo.", "cited_ids": []}
    return {
        "answer": str(result.get("answer", "")),
        "cited_ids": [str(i) for i in (result.get("cited_ids") or [])],
    }
