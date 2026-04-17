"""Batch-classify Anytype items that have no classified_at set.

One subprocess call handles up to ~150 items by sending them as a compact
list and asking the LLM to return a JSON array keyed by object id.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger

from config.labels import AREAS, PRIORIDADES, TAGS, taxonomy_prompt_block
from config.settings import TIMEZONE
from services.ai_subprocess import AISubprocessError, run_claude

BATCH_SIZE = 80  # keep prompts well under context limits


def _item_line(obj: dict) -> str:
    oid = obj.get("id", "")[:10]
    name = (obj.get("name") or "").replace("\n", " ")[:120]
    snippet = (obj.get("snippet") or "").replace("\n", " ")[:120]
    tkey = obj.get("_type_key", "?")
    return f"- {oid} [{tkey}] {name}" + (f" | {snippet}" if snippet else "")


def _build_prompt(items: list[dict]) -> str:
    now = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")
    lines = [_item_line(o) for o in items]
    return f"""You classify Portuguese productivity items (tasks, notes, appointments) into a fixed taxonomy. Today: {now}.

{taxonomy_prompt_block()}

Rules:
- Every item MUST get exactly one area and one prioridade.
- Tags are zero or more from the TAGS list — never invent new ones.
- If you truly cannot classify, use area="pessoal", prioridade="baixa", tags=[].

Items to classify:
{chr(10).join(lines)}

Return a JSON array with one object per item, in the same order, each shaped like:
{{"id": "<full id prefix shown above>", "area": "...", "prioridade": "...", "tags": [...]}}

Return ONLY the JSON array, no prose, no code fences."""


def clamp_to_taxonomy(result: dict) -> dict:
    """Clamp area/prioridade/tags to the taxonomy so bogus values don't leak."""
    area = result.get("area", "pessoal")
    if area not in AREAS:
        area = "pessoal"
    pri = result.get("prioridade", "baixa")
    if pri not in PRIORIDADES:
        pri = "baixa"
    tags = [t for t in (result.get("tags") or []) if t in TAGS]
    return {"area": area, "prioridade": pri, "tags": tags}


async def classify_batch(anytype_client, items: list[dict]) -> dict:
    """Classify items in one LLM call and apply the results in Anytype.

    Returns counts: {classified, failed, by_area: {area: count}}.
    """
    if not items:
        return {"classified": 0, "failed": 0, "by_area": {}}

    prompt = _build_prompt(items)
    try:
        result = await run_claude(prompt, timeout=120.0)
    except AISubprocessError as e:
        logger.error("Classifier subprocess failed: {}", e)
        return {"classified": 0, "failed": len(items), "by_area": {}}

    if not isinstance(result, list):
        logger.error("Classifier returned non-list: {}", type(result).__name__)
        return {"classified": 0, "failed": len(items), "by_area": {}}

    # Index results by the id prefix the LLM echoed back
    by_prefix: dict[str, dict] = {}
    for r in result:
        if isinstance(r, dict) and r.get("id"):
            by_prefix[r["id"][:10]] = r

    now_iso = datetime.now(ZoneInfo(TIMEZONE)).isoformat()
    classified = 0
    failed = 0
    by_area: dict[str, int] = {}

    for obj in items:
        full_id = obj.get("id", "")
        prefix = full_id[:10]
        r = by_prefix.get(prefix)
        if not r:
            failed += 1
            continue
        clean = clamp_to_taxonomy(r)
        ok = anytype_client.set_classification(
            full_id,
            area=clean["area"],
            prioridade=clean["prioridade"],
            tags=clean["tags"],
            classified_at=now_iso,
        )
        if ok:
            classified += 1
            by_area[clean["area"]] = by_area.get(clean["area"], 0) + 1
        else:
            failed += 1

    return {"classified": classified, "failed": failed, "by_area": by_area}


async def classify_unclassified(anytype_client) -> dict:
    """Fetch unclassified items and process them in batches.

    Returns aggregate counts across all batches.
    """
    items = anytype_client.list_unclassified()
    if not items:
        return {"classified": 0, "failed": 0, "by_area": {}, "total": 0}

    agg = {"classified": 0, "failed": 0, "by_area": {}, "total": len(items)}
    for i in range(0, len(items), BATCH_SIZE):
        batch = items[i : i + BATCH_SIZE]
        counts = await classify_batch(anytype_client, batch)
        agg["classified"] += counts["classified"]
        agg["failed"] += counts["failed"]
        for k, v in counts["by_area"].items():
            agg["by_area"][k] = agg["by_area"].get(k, 0) + v
    return agg
