"""Set up the 5-day weekly workout plan in Anytype + Google Calendar.

Creates one Anytype Page per weekday (Mon-Fri) with the workout structure
as a markdown body: mobility, pyramid warm-up, main lift (5x5 or 2x5),
accessory/core, and a "Histórico" section for manual log entries.

Optionally creates 5 weekly-recurring Google Calendar events with a one-line
summary of each day's main lift.

Sequence: rotated -1 from the original template so Friday's pull-up day
lands on Monday. Then the order continues:
  Mon  Barra Fixa (Carga)
  Ter  Agachamento
  Qua  Supino Reto
  Qui  Levantamento Terra (2x5)
  Sex  Militar (OHP)

Persistence model: ONE object per weekday (Option B from design). The
"Carga atual" line is edited in-place each session — it always reflects
the most recent load. Past sessions go in the "Histórico" section,
appended manually (or by a future agent) as one line per session in a
parseable pipe-delimited format.

No automatic timer. Use a separate interval-timer app on the phone
(2 min rest between main-lift sets, 1 min between accessory sets).

Prerequisites:
    - Anytype desktop app running, local API enabled (Settings → Local API)
    - ANYTYPE_API_KEY + ANYTYPE_SPACE_ID set in .env (run setup_anytype.py)
    - For --calendar: data/token.json present (run setup_google.py)

Usage:
    cd productivity
    python scripts/setup_workout.py                 # Anytype only
    python scripts/setup_workout.py --calendar      # also create recurring GCal events
    python scripts/setup_workout.py --calendar --time 06:30 --duration 75
    python scripts/setup_workout.py --force         # recreate even if objects exist
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from config.settings import TIMEZONE
from integrations.anytype_client import AnytypeClient


# --- Workout definitions ---
# Order: starts with Friday's pull-up day on Monday, then rotates.
WORKOUTS: list[dict] = [
    {
        "weekday_pt": "Segunda",
        "byday": "MO",
        "weekday_idx": 0,  # Monday=0 in datetime.weekday()
        "main": {
            "name": "Barra Fixa",
            "sets": 5,
            "reps": 5,
            "weight_label": "Carga (lastro)",
            "uses_weight": True,
        },
        "mobility": ["Alongamento de punhos", "Espalmada na parede"],
        "accessories": [
            {"name": "Paralelas", "sets": 3, "weight_label": "Carga (lastro)", "uses_weight": True},
            {"name": "Abdominal Bicicleta", "sets": 3, "uses_weight": False},
        ],
    },
    {
        "weekday_pt": "Terça",
        "byday": "TU",
        "weekday_idx": 1,
        "main": {
            "name": "Agachamento",
            "sets": 5,
            "reps": 5,
            "weight_label": "Carga (kg)",
            "uses_weight": True,
        },
        "mobility": ["Agachamento profundo (hold)", "Cossack Squat"],
        "accessories": [
            {"name": "Abdominal Supra (com carga)", "sets": 3, "weight_label": "Carga (kg)", "uses_weight": True},
        ],
    },
    {
        "weekday_pt": "Quarta",
        "byday": "WE",
        "weekday_idx": 2,
        "main": {
            "name": "Supino Reto",
            "sets": 5,
            "reps": 5,
            "weight_label": "Carga (kg)",
            "uses_weight": True,
        },
        "mobility": ["Rotação de ombros (bastão)", "Alongamento dinâmico de peito"],
        "accessories": [
            {"name": "Barra Fixa", "sets": 3, "uses_weight": False},
            {"name": "Abdominal Oblíquo", "sets": 3, "uses_weight": False},
        ],
    },
    {
        "weekday_pt": "Quinta",
        "byday": "TH",
        "weekday_idx": 3,
        "main": {
            "name": "Levantamento Terra",
            "sets": 2,
            "reps": 5,
            "weight_label": "Carga (kg)",
            "uses_weight": True,
        },
        "mobility": ["World's Greatest Stretch", "Good Morning (só barra)"],
        "accessories": [
            {"name": "Abdominal Infra (Elevação)", "sets": 3, "uses_weight": False},
        ],
    },
    {
        "weekday_pt": "Sexta",
        "byday": "FR",
        "weekday_idx": 4,
        "main": {
            "name": "Militar (OHP)",
            "sets": 5,
            "reps": 5,
            "weight_label": "Carga (kg)",
            "uses_weight": True,
        },
        "mobility": ["Rotação de escápulas", "Mobilidade Torácica (parede)"],
        "accessories": [
            {"name": "Remada Curvada", "sets": 3, "weight_label": "Carga (kg)", "uses_weight": True},
            {"name": "Prancha", "sets": 3, "uses_weight": False},
        ],
    },
]

WORKOUT_ICON = "🏋"


def build_body(workout: dict) -> str:
    """Render the markdown body for a single workout day.

    Format is consistent across days so a downstream agent can parse it.
    """
    main = workout["main"]
    weekday = workout["weekday_pt"]
    sets, reps = main["sets"], main["reps"]

    lines: list[str] = []
    lines.append(f"# Treino - {weekday} - {main['name']}")
    lines.append("")
    lines.append("## Mobilidade (5 min)")
    for mob in workout["mobility"]:
        lines.append(f"- [ ] {mob}")
    lines.append("")

    lines.append(f"## Aquecimento em Pirâmide — {main['name']}")
    if main["uses_weight"]:
        lines.append("- [ ] Série 1: 10 reps só com a barra (ou só corpo)")
        lines.append("- [ ] Série 2: 5 reps a 40% da carga final")
        lines.append("- [ ] Série 3: 3 reps a 70% da carga final")
    else:
        lines.append("- [ ] Série 1: 10 reps leves")
        lines.append("- [ ] Série 2: 5 reps moderadas")
        lines.append("- [ ] Série 3: 3 reps próximas da carga final")
    lines.append("")

    lines.append(f"## Levantamento Principal: {main['name']} ({sets}x{reps})")
    if main["uses_weight"]:
        lines.append(f"**{main['weight_label']} atual:** _______")
        lines.append("")
    for i in range(1, sets + 1):
        lines.append(f"- [ ] Série {i}")
    lines.append("")
    lines.append("> Descanso: 2 min entre séries")
    lines.append("")

    lines.append("## Acessório / Core")
    for acc in workout["accessories"]:
        lines.append(f"### {acc['name']}")
        if acc.get("uses_weight"):
            lines.append(f"**{acc['weight_label']} atual:** _______")
            lines.append("")
        for i in range(1, acc["sets"] + 1):
            lines.append(f"- [ ] Série {i}")
        lines.append("")
    lines.append("> Descanso: 1 min entre séries")
    lines.append("")

    lines.append("## Histórico")
    lines.append(
        "<!-- Adicione uma linha ao final de cada sessão. Formato sugerido:\n"
        "YYYY-MM-DD | Principal: SxR @ XXkg | AcessorioN: SxR @ YYkg | obs\n"
        "Exemplo:\n"
        f"2026-05-11 | {main['name']}: {sets}x{reps} @ 20kg | Acessório: 3x10 @ 5kg | facil\n"
        "-->"
    )
    lines.append("")
    return "\n".join(lines)


def object_name(workout: dict) -> str:
    return f"Treino - {workout['weekday_pt']} - {workout['main']['name']}"


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
            logger.info("Skipping (already exists): {} → {}", name, existing)
            created[w["weekday_pt"]] = existing
            continue

        body = build_body(w)
        description = (
            f"Treino de {w['weekday_pt'].lower()}: "
            f"{w['main']['name']} {w['main']['sets']}x{w['main']['reps']}."
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
            logger.info("Created: {} → {}", name, obj_id)
        else:
            logger.error("Failed to create: {}", name)

    client.close()
    return created


def create_calendar_events(time_str: str, duration_min: int) -> list[str]:
    """Create 5 weekly-recurring calendar events, one per workout day.

    time_str: 'HH:MM' for the workout start time (local timezone).
    duration_min: event length.
    Returns list of created event IDs.
    """
    # Lazy import — Google libs are heavy and optional.
    from integrations.google_calendar import GoogleCalendarClient

    gc = GoogleCalendarClient()
    gc.authenticate()

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

        title = f"{WORKOUT_ICON} Treino - {w['main']['name']}"
        accessories = ", ".join(a["name"] for a in w["accessories"])
        description = (
            f"Mobilidade: {', '.join(w['mobility'])}\n"
            f"Principal: {w['main']['name']} {w['main']['sets']}x{w['main']['reps']}\n"
            f"Acessórios: {accessories}\n"
            f"Descanso: 2 min (principal) / 1 min (acessórios)"
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

    return created_ids


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up weekly workout plan in Anytype.")
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
        default=75,
        help="Event duration in minutes (default 75). Only used with --calendar.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Recreate Anytype pages even if a same-named page already exists.",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Workout setup")
    print("=" * 60)

    pages = create_anytype_pages(force=args.force)
    print(f"\nAnytype: {len(pages)}/{len(WORKOUTS)} pages ready.")
    for weekday, obj_id in pages.items():
        print(f"  - {weekday}: {obj_id}")

    if args.calendar:
        print(f"\nCreating recurring events at {args.time} ({args.duration} min)...")
        event_ids = create_calendar_events(args.time, args.duration)
        print(f"Calendar: {len(event_ids)}/{len(WORKOUTS)} recurring events created.")
        for eid in event_ids:
            print(f"  - {eid}")

    print("\nDone.")


if __name__ == "__main__":
    main()
