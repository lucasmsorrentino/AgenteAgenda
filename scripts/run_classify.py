"""Classify unclassified Anytype items in one batch via Claude Code subprocess.

Runs on demand only (no scheduler). Called by the /classificar Telegram
command, or manually from the CLI for ad-hoc runs.

Usage:
    cd productivity
    python scripts/run_classify.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from config.settings import ANYTYPE_API_KEY, ANYTYPE_SPACE_ID
from integrations.anytype_client import AnytypeClient
from services.ai_classifier import classify_unclassified


async def _run() -> int:
    if not ANYTYPE_API_KEY or not ANYTYPE_SPACE_ID:
        print("ANYTYPE_API_KEY/ANYTYPE_SPACE_ID nao configurados no .env")
        return 1

    anytype = AnytypeClient()
    if not anytype.verify_connection():
        print("Nao consegui conectar no Anytype.")
        return 1

    try:
        counts = await classify_unclassified(anytype)
    finally:
        anytype.close()

    if counts["total"] == 0:
        print("Nada a classificar — todos os itens ja tem label.")
        return 0

    logger.info(
        "Classificacao: {} processados, {} classificados, {} falhas",
        counts["total"], counts["classified"], counts["failed"],
    )
    print(f"Processados: {counts['total']}")
    print(f"Classificados: {counts['classified']}")
    print(f"Falhas: {counts['failed']}")
    if counts["by_area"]:
        print("Por area:")
        for area, n in sorted(counts["by_area"].items(), key=lambda x: -x[1]):
            print(f"  {area}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
