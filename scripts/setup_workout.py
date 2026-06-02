"""Set up the weekly home/bodyweight workout plan (ABCDE) in Anytype + Google Calendar.

Creates one Anytype Page per weekday (Mon-Fri), each holding one of the five
training days from the "Treino Casa ABCDE" plan (peso corporal, sem academia):

  Seg  Treino A — Inferiores (Quadríceps / Glúteo)
  Ter  Treino B — Empurrar (Peito / Ombro / Tríceps)
  Qua  Treino C — Puxar (Costas / Bíceps)
  Qui  Treino D — Cadeia Posterior + Core
  Sex  Treino E — Metabólico (circuito, 4-5 voltas)

Each page renders the exercises as checkbox blocks (one per série / volta) so you
can tick them off during the session, plus a "Carga (mochila)" placeholder and a
"## Histórico" section for manual session logs in a parseable pipe format.

Optionally creates 5 weekly-recurring Google Calendar events (one per workout day).

Replacement ("swap") model — runs BY DEFAULT:
  This script replaces any previous workout plan. It deletes Anytype pages whose
  name starts with "Treino - " that aren't part of the new ABCDE set, and (with
  --calendar) cancels the old recurring "Treino" calendar series, before/while
  creating the new ones. Use --keep-old to only add the new plan without removing
  the previous one.

Persistence model: ONE object per weekday. The "Carga (mochila)" line is edited
in-place each session — it always reflects the most recent load. Past sessions go
in the "## Histórico" section, appended manually (or by a future agent).

No automatic timer. Use a separate interval-timer app on the phone
(60-90s rest between séries; little/no rest inside the metabólico circuit,
60-90s between voltas).

Prerequisites:
    - Anytype desktop app running, local API enabled (Settings -> Local API)
    - ANYTYPE_API_KEY + ANYTYPE_SPACE_ID set in .env (run setup_anytype.py)
    - For --calendar: data/token.json present (run setup_google.py)

Usage:
    cd productivity
    python scripts/setup_workout.py                 # Anytype only, swap old plan
    python scripts/setup_workout.py --calendar      # also swap recurring GCal events
    python scripts/setup_workout.py --calendar --time 06:30 --duration 60
    python scripts/setup_workout.py --keep-old      # add new plan, keep old pages
    python scripts/setup_workout.py --force         # recreate new pages even if they exist
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from config.settings import TIMEZONE
from integrations.anytype_client import AnytypeClient


# --- Workout definitions (Treino Casa ABCDE, peso corporal) ---
# Mapped to weekdays: A=Seg, B=Ter, C=Qua, D=Qui, E=Sex.
COMMON_NOTE = (
    "Sem peso? Aumente a dificuldade pela cadência lenta, pausa isométrica e "
    "versão unilateral antes de só subir peso (mochila com carga)."
)

WORKOUTS: list[dict] = [
    {
        "weekday_pt": "Segunda",
        "byday": "MO",
        "weekday_idx": 0,  # Monday=0 in datetime.weekday()
        "letter": "A",
        "focus": "Inferiores",
        "muscles": "Quadríceps / Glúteo",
        "exercises": [
            {"name": "Agachamento (cadência lenta, 4s descida)", "sets": "4", "reps": "15"},
            {"name": "Búlgaro (pé traseiro na cadeira)", "sets": "4", "reps": "10 / perna"},
            {"name": "Pistol assistido / agachamento 1 perna", "sets": "3", "reps": "6 / perna"},
            {"name": "Panturrilha unilateral no degrau", "sets": "4", "reps": "15 / perna"},
            {"name": "Parede sentada (wall sit)", "sets": "3", "reps": "45s"},
        ],
    },
    {
        "weekday_pt": "Terça",
        "byday": "TU",
        "weekday_idx": 1,
        "letter": "B",
        "focus": "Empurrar",
        "muscles": "Peito / Ombro / Tríceps",
        "exercises": [
            {"name": "Flexão de braços com pés elevados", "sets": "4", "reps": "máx"},
            {"name": "Flexão diamante (tríceps)", "sets": "3", "reps": "máx"},
            {"name": "Pike push-up (quadril alto, ombro)", "sets": "3", "reps": "10"},
            {"name": "Flexão archer / com pausa 2s embaixo", "sets": "3", "reps": "8"},
            {"name": "Dips na cadeira (tríceps)", "sets": "3", "reps": "máx"},
        ],
    },
    {
        "weekday_pt": "Quarta",
        "byday": "WE",
        "weekday_idx": 2,
        "letter": "C",
        "focus": "Puxar",
        "muscles": "Costas / Bíceps",
        "exercises": [
            {"name": "Remada invertida embaixo da mesa", "sets": "4", "reps": "máx"},
            {"name": "Barra na porta (se tiver)", "sets": "3", "reps": "máx"},
            {"name": "Remada toalha na porta (isométrica)", "sets": "3", "reps": "20s"},
            {"name": "Superman", "sets": "3", "reps": "15"},
            {"name": "Prancha", "sets": "3", "reps": "45s"},
        ],
    },
    {
        "weekday_pt": "Quinta",
        "byday": "TH",
        "weekday_idx": 3,
        "letter": "D",
        "focus": "Cadeia Posterior + Core",
        "muscles": "Posteriores / Glúteo / Abdômen",
        "exercises": [
            {"name": "Ponte de glúteo unilateral (cadência lenta)", "sets": "4", "reps": "12 / perna"},
            {"name": "Stiff numa perna (equilíbrio)", "sets": "4", "reps": "10 / perna"},
            {"name": "Nórdico assistido / leg curl deslizante", "sets": "3", "reps": "8"},
            {"name": "Elevação de pernas deitado", "sets": "3", "reps": "15"},
            {"name": "Bird-dog com pausa", "sets": "3", "reps": "10 / lado"},
        ],
    },
    {
        "weekday_pt": "Sexta",
        "byday": "FR",
        "weekday_idx": 4,
        "letter": "E",
        "focus": "Metabólico",
        "muscles": "Circuito — gasto calórico",
        "circuit": True,
        "rounds": 5,  # 4-5 voltas
        "exercises": [
            {"name": "Burpee", "sets": "4-5", "reps": "12"},
            {"name": "Agachamento com salto", "sets": "4-5", "reps": "20"},
            {"name": "Flexão de braços", "sets": "4-5", "reps": "12"},
            {"name": "Mountain climber", "sets": "4-5", "reps": "40s"},
            {"name": "Afundo alternado saltado", "sets": "4-5", "reps": "16"},
            {"name": "Prancha", "sets": "4-5", "reps": "45s"},
        ],
    },
]

WORKOUT_ICON = "🏋"
# Pages following the workout naming convention. Used by the swap/cleanup step to
# decide which legacy pages to remove.
WORKOUT_PREFIX = "Treino - "


def _set_count(sets_str: str) -> int:
    """Extract a checkbox count from a sets label ('4' -> 4, '4-5' -> 5)."""
    nums = re.findall(r"\d+", sets_str)
    return int(nums[-1]) if nums else 1


def build_body(workout: dict) -> str:
    """Render the markdown body for a single workout day.

    Format is consistent across days so a downstream agent can parse it.
    Non-circuit days get one checkbox per série; the metabólico circuit gets
    one checkbox per volta.
    """
    letter = workout["letter"]
    weekday = workout["weekday_pt"]
    focus = workout["focus"]
    muscles = workout["muscles"]
    is_circuit = workout.get("circuit", False)

    lines: list[str] = []
    lines.append(f"# Treino {letter} — {focus}")
    lines.append(f"**{weekday} · {muscles}**")
    lines.append("")
    lines.append("Treino em casa · peso corporal")
    lines.append("")

    lines.append("## Mobilidade (5 min)")
    lines.append("- [ ] Articular geral + ativação do grupo do dia")
    lines.append("")

    if is_circuit:
        rounds = workout.get("rounds", 5)
        lines.append("## Circuito metabólico — 4-5 voltas")
        lines.append("Sequência sem descanso entre exercícios; 60-90s entre voltas.")
        lines.append("")
        for i, ex in enumerate(workout["exercises"], start=1):
            lines.append(f"{i}. {ex['name']} — {ex['reps']}")
        lines.append("")
        lines.append("### Voltas")
        for r in range(1, rounds + 1):
            lines.append(f"- [ ] Volta {r}")
        lines.append("")
        lines.append("> Descanso: nenhum entre exercícios · 60-90s entre voltas")
        lines.append("")
    else:
        lines.append("## Exercícios")
        lines.append("")
        for ex in workout["exercises"]:
            lines.append(f"### {ex['name']}")
            lines.append(f"**Alvo:** {ex['sets']} x {ex['reps']}  ·  Carga (mochila): _______")
            for s in range(1, _set_count(ex["sets"]) + 1):
                lines.append(f"- [ ] Série {s}")
            lines.append("")
        lines.append("> Descanso: 60-90s entre séries")
        lines.append("")

    lines.append("## Notas / Carga")
    lines.append(COMMON_NOTE)
    lines.append("")

    lines.append("## Histórico")
    lines.append(
        "<!-- Adicione uma linha ao final de cada sessão. Formato sugerido:\n"
        "YYYY-MM-DD | Exercicio: SxR @ mochila Xkg | ... | obs\n"
        "Exemplo:\n"
        f"2026-06-08 | {workout['exercises'][0]['name']}: "
        f"{workout['exercises'][0]['sets']}x{workout['exercises'][0]['reps']} @ mochila 5kg | facil\n"
        "-->"
    )
    lines.append("")
    return "\n".join(lines)


def object_name(workout: dict) -> str:
    return f"{WORKOUT_PREFIX}{workout['weekday_pt']} - {workout['letter']}: {workout['focus']}"


def event_title(workout: dict) -> str:
    return f"{WORKOUT_ICON} Treino {workout['letter']} - {workout['focus']}"


def find_existing(client: AnytypeClient, name: str) -> str | None:
    """Return object_id if a page with this exact name already exists."""
    try:
        results = client.search_objects(query=name, types=["page"], limit=20)
        for obj in results:
            if obj.get("name", "").strip() == name.strip():
                return obj.get("id")
    except Exception as e:
        logger.warning("search_objects failed for '{}': {}", name, e)
    return None


def cleanup_old_pages(client: AnytypeClient, keep_names: set[str]) -> list[str]:
    """Delete legacy workout pages so the new plan replaces the old one.

    Targets pages whose name starts with the workout prefix ("Treino - ") that
    are NOT part of the new ABCDE set. This swaps out a previous plan regardless
    of the exact old page names. Pages the user created outside this convention
    are untouched.
    """
    removed: list[str] = []
    try:
        results = client.search_objects(query=WORKOUT_PREFIX, types=["page"], limit=100)
    except Exception as e:
        logger.warning("Could not search old workout pages: {}", e)
        return removed

    for obj in results:
        name = (obj.get("name") or "").strip()
        if not name.startswith(WORKOUT_PREFIX) or name in keep_names:
            continue
        obj_id = obj.get("id")
        if obj_id and client.delete_object(obj_id):
            removed.append(name)
            logger.info("Removed old workout page: {} ({})", name, obj_id)
    return removed


def create_anytype_pages(force: bool = False) -> dict[str, str]:
    """Create one Anytype Page per workout day.

    Returns {weekday_pt: object_id} for the pages that exist after this call
    (newly created or pre-existing). Skips creation if a page with the same
    name exists, unless force=True.
    """
    client = AnytypeClient()
    if not client.verify_connection():
        logger.error("Anytype API unreachable — make sure the desktop app is open")
        sys.exit(2)

    created: dict[str, str] = {}
    for w in WORKOUTS:
        name = object_name(w)
        existing = find_existing(client, name)
        if existing and not force:
            logger.info("Skipping (already exists): {} -> {}", name, existing)
            created[w["weekday_pt"]] = existing
            continue

        body = build_body(w)
        description = (
            f"Treino {w['letter']} ({w['weekday_pt'].lower()}): "
            f"{w['focus']} — {w['muscles']}."
        )
        obj_id = client.create_object(
            type_key="page",
            name=name,
            body=body,
            icon=WORKOUT_ICON,
            description=description,
        )
        if obj_id:
            created[w["weekday_pt"]] = obj_id
            logger.info("Created: {} -> {}", name, obj_id)
        else:
            logger.error("Failed to create: {}", name)

    client.close()
    return created


def swap_anytype_pages(force: bool = False) -> tuple[dict[str, str], list[str]]:
    """Create the new pages and remove any legacy workout pages."""
    created = create_anytype_pages(force=force)
    client = AnytypeClient()
    if not client.verify_connection():
        return created, []
    keep = {object_name(w) for w in WORKOUTS}
    removed = cleanup_old_pages(client, keep)
    client.close()
    return created, removed


def cleanup_old_events(gc, keep_titles: set[str]) -> list[str]:
    """Cancel legacy recurring 'Treino' calendar series before creating new ones."""
    removed: list[str] = []
    seen_series: set[str] = set()
    try:
        events = gc.get_all_events_range(days_back=0, days_forward=14)
    except Exception as e:
        logger.warning("Could not list calendar events for cleanup: {}", e)
        return removed

    for ev in events:
        if "Treino" not in ev.title or ev.title in keep_titles:
            continue
        key = ev.recurring_event_id or ev.id
        if key in seen_series:
            continue
        seen_series.add(key)
        try:
            gc.delete_event(ev.id, scope="all")
            removed.append(ev.title)
            logger.info("Cancelled old workout series: {}", ev.title)
        except Exception as e:
            logger.warning("Failed to cancel old event '{}': {}", ev.title, e)
    return removed


def create_calendar_events(time_str: str, duration_min: int, keep_old: bool = False) -> tuple[list[str], list[str]]:
    """Create 5 weekly-recurring calendar events, one per workout day.

    time_str: 'HH:MM' for the workout start time (local timezone).
    duration_min: event length.
    keep_old: when False (default), cancels any previous 'Treino' series first.
    Returns (created_event_ids, removed_old_titles).
    """
    # Lazy import — Google libs are heavy and optional.
    from integrations.google_calendar import GoogleCalendarClient

    gc = GoogleCalendarClient()
    gc.authenticate()

    keep_titles = {event_title(w) for w in WORKOUTS}
    removed: list[str] = []
    if not keep_old:
        removed = cleanup_old_events(gc, keep_titles)

    tz = ZoneInfo(TIMEZONE)
    hour, minute = map(int, time_str.split(":"))

    # Anchor on the next occurrence of each weekday so the recurrence starts cleanly.
    today = datetime.now(tz).date()
    created_ids: list[str] = []

    for w in WORKOUTS:
        # First occurrence: next date whose weekday matches w["weekday_idx"]
        delta = (w["weekday_idx"] - today.weekday()) % 7
        first_date = today + timedelta(days=delta)
        start = datetime(
            first_date.year, first_date.month, first_date.day, hour, minute, tzinfo=tz
        )
        end = start + timedelta(minutes=duration_min)

        title = event_title(w)
        exercises = ", ".join(e["name"] for e in w["exercises"])
        if w.get("circuit"):
            rest = "Descanso: nenhum entre exercícios / 60-90s entre voltas"
        else:
            rest = "Descanso: 60-90s entre séries"
        description = (
            f"Treino {w['letter']} — {w['focus']} ({w['muscles']})\n"
            f"Exercícios: {exercises}\n"
            f"{rest}\n"
            f"Peso corporal — em casa."
        )
        rrule = [f"RRULE:FREQ=WEEKLY;BYDAY={w['byday']}"]
        try:
            event_id = gc.create_event(
                title=title,
                start=start,
                end=end,
                description=description,
                recurrence=rrule,
            )
            created_ids.append(event_id)
        except Exception as e:
            logger.error("Failed to create calendar event for {}: {}", w["weekday_pt"], e)

    return created_ids, removed


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up the home ABCDE workout plan in Anytype.")
    parser.add_argument(
        "--calendar",
        action="store_true",
        help="Also create 5 weekly-recurring events in Google Calendar.",
    )
    parser.add_argument(
        "--time",
        default="18:00",
        help="Workout start time HH:MM (default 18:00). Only used with --calendar.",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=60,
        help="Event duration in minutes (default 60). Only used with --calendar.",
    )
    parser.add_argument(
        "--keep-old",
        action="store_true",
        help="Add the new plan without removing the previous workout pages/events.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Recreate Anytype pages even if a same-named page already exists.",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Workout setup — Treino Casa ABCDE (peso corporal)")
    print("=" * 60)

    if args.keep_old:
        pages = create_anytype_pages(force=args.force)
        removed_pages: list[str] = []
    else:
        pages, removed_pages = swap_anytype_pages(force=args.force)

    print(f"\nAnytype: {len(pages)}/{len(WORKOUTS)} pages ready.")
    for weekday, obj_id in pages.items():
        print(f"  - {weekday}: {obj_id}")
    if removed_pages:
        print(f"Removed {len(removed_pages)} old workout page(s):")
        for name in removed_pages:
            print(f"  - {name}")

    if args.calendar:
        action = "Replacing" if not args.keep_old else "Creating"
        print(f"\n{action} recurring events at {args.time} ({args.duration} min)...")
        event_ids, removed_events = create_calendar_events(
            args.time, args.duration, keep_old=args.keep_old
        )
        if removed_events:
            print(f"Cancelled {len(removed_events)} old recurring series:")
            for t in removed_events:
                print(f"  - {t}")
        print(f"Calendar: {len(event_ids)}/{len(WORKOUTS)} recurring events created.")
        for eid in event_ids:
            print(f"  - {eid}")

    print("\nDone.")


if __name__ == "__main__":
    main()
