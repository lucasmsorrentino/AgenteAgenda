"""Fixed taxonomy used by the AI classifier.

Keeping the taxonomy closed prevents label proliferation — the LLM must pick
from these lists only. Edit here to customize; changes take effect next sync.
"""

from __future__ import annotations

# Single primary area per item (select)
AREAS: list[str] = [
    "tcc",
    "faculdade",
    "trabalho",
    "pessoal",
    "saude",
    "financas",
    "casa",
    "lazer",
]

# Priority (select) — derived by AI from deadline + urgency words
PRIORIDADES: list[str] = ["alta", "media", "baixa"]

# Free-form-ish tags (multi_select) — still constrained to this list
TAGS: list[str] = [
    "urgente",
    "rotina",
    "estudo",
    "prova",
    "entrega",
    "reuniao",
    "compra",
    "viagem",
    "consulta",
    "exercicio",
    "leitura",
    "projeto",
]


def taxonomy_prompt_block() -> str:
    """Format the taxonomy for inclusion in LLM prompts."""
    return (
        f"AREAS (choose exactly one): {', '.join(AREAS)}\n"
        f"PRIORIDADES (choose exactly one): {', '.join(PRIORIDADES)}\n"
        f"TAGS (choose zero or more): {', '.join(TAGS)}"
    )
