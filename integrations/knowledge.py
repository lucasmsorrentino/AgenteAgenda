"""Knowledge-store factory.

Single place that decides which knowledge backend the bot uses. Default is
Obsidian (filesystem vault); Anytype is only used when explicitly selected via
KNOWLEDGE_BACKEND=anytype *and* its API key/space are configured.

Swap history: Anytype was the original store. It required the Anytype desktop
app running with the local API enabled, which never fit an always-on headless
box. Obsidian is a plain markdown vault (already git-synced Windows<->Android),
so the bot writes tasks/appointments straight into the vault with no daemon.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from config.settings import (
    ANYTYPE_API_KEY,
    ANYTYPE_SPACE_ID,
    KNOWLEDGE_BACKEND,
    OBSIDIAN_VAULT_PATH,
)


def get_knowledge_client(verbose: bool = True):
    """Return the configured knowledge client, or None if none is available.

    Selection:
      - KNOWLEDGE_BACKEND=anytype -> try Anytype (needs API key + space id),
        fall back to Obsidian if offline.
      - anything else (default "obsidian") -> Obsidian vault.
    """
    backend = (KNOWLEDGE_BACKEND or "obsidian").strip().lower()

    if backend == "anytype":
        if ANYTYPE_API_KEY and ANYTYPE_SPACE_ID:
            from integrations.anytype_client import AnytypeClient

            anytype = AnytypeClient()
            if anytype.verify_connection():
                if verbose:
                    logger.info("Knowledge store: Anytype")
                return anytype
            logger.warning("Anytype offline — falling back to Obsidian")
        else:
            logger.warning(
                "KNOWLEDGE_BACKEND=anytype but API key/space missing — using Obsidian"
            )

    from integrations.obsidian_client import ObsidianClient

    vault = Path(OBSIDIAN_VAULT_PATH)
    obsidian = ObsidianClient(vault)
    if obsidian.verify_connection():
        if verbose:
            logger.info("Knowledge store: Obsidian ({})", vault)
        return obsidian

    logger.warning("Obsidian vault unavailable at {} — no knowledge store", vault)
    return None
