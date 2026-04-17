"""Add Compromisso type + properties to an already-authenticated Anytype space.

Idempotent: existing properties/types return 409 and are skipped. Updates
data/anytype_schema.json with the new keys so anytype_client.py can resolve
'compromisso' to the custom type.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from config.settings import ANYTYPE_API_KEY, ANYTYPE_API_VERSION, ANYTYPE_BASE_URL, ANYTYPE_SPACE_ID

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "data" / "anytype_schema.json"

NEW_PROPERTIES = [
    ("start", "date"),
    ("end", "date"),
    ("location", "text"),
    ("recurring", "checkbox"),
    ("classified_at", "date"),
    ("area", "select"),
    ("prioridade", "select"),
]
NEW_TYPES = [
    # (name, plural, emoji, layout) — Anytype API requires plural_name and icon as object
    ("Compromisso", "Compromissos", "📅", "basic"),
]


def main() -> int:
    if not ANYTYPE_API_KEY or not ANYTYPE_SPACE_ID:
        print("ANYTYPE_API_KEY/ANYTYPE_SPACE_ID nao configurados no .env")
        return 1

    headers = {
        "Authorization": f"Bearer {ANYTYPE_API_KEY}",
        "Anytype-Version": ANYTYPE_API_VERSION,
        "Content-Type": "application/json",
    }
    base = f"{ANYTYPE_BASE_URL}/v1/spaces/{ANYTYPE_SPACE_ID}"

    schema = {"types": {}, "properties": {}}
    if SCHEMA_FILE.exists():
        try:
            schema = json.loads(SCHEMA_FILE.read_text())
            schema.setdefault("types", {})
            schema.setdefault("properties", {})
        except Exception:
            pass

    try:
        httpx.get(f"{ANYTYPE_BASE_URL}/v1/spaces", headers=headers, timeout=5).raise_for_status()
    except Exception as e:
        print(f"Falha ao conectar no Anytype ({ANYTYPE_BASE_URL}): {e}")
        print("Verifique se o app Anytype esta aberto e a Local API ativada.")
        return 1

    print("Criando propriedades...")
    for name, fmt in NEW_PROPERTIES:
        if name in schema["properties"]:
            print(f"  - {name}: ja no schema ({schema['properties'][name]})")
            continue
        try:
            r = httpx.post(f"{base}/properties", json={"name": name, "format": fmt}, headers=headers)
            r.raise_for_status()
            key = r.json().get("property", {}).get("key", "")
            schema["properties"][name] = key
            print(f"  + {name} ({fmt}) -> {key}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 409:
                # Already exists — fetch its key from /properties listing
                try:
                    listing = httpx.get(f"{base}/properties", headers=headers).json().get("data", [])
                    for p in listing:
                        if p.get("name", "").lower() == name.lower():
                            schema["properties"][name] = p.get("key", "")
                            print(f"  = {name}: ja existia, key={p.get('key', '')}")
                            break
                except Exception:
                    print(f"  ! {name}: 409 mas nao consegui resolver a key")
            else:
                print(f"  X {name}: {e}")

    print("Criando tipos...")
    for name, plural, emoji, layout in NEW_TYPES:
        if name in schema["types"]:
            print(f"  - {name}: ja no schema ({schema['types'][name]})")
            continue
        try:
            payload = {
                "name": name,
                "plural_name": plural,
                "icon": {"format": "emoji", "emoji": emoji},
                "layout": layout,
            }
            r = httpx.post(f"{base}/types", json=payload, headers=headers)
            r.raise_for_status()
            key = r.json().get("type", {}).get("key", "")
            schema["types"][name] = key
            print(f"  + {name} -> {key}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 409:
                try:
                    listing = httpx.get(f"{base}/types", headers=headers).json().get("data", [])
                    for t in listing:
                        if t.get("name", "").lower() == name.lower():
                            schema["types"][name] = t.get("key", "")
                            print(f"  = {name}: ja existia, key={t.get('key', '')}")
                            break
                except Exception:
                    print(f"  ! {name}: 409 mas nao consegui resolver a key")
            else:
                print(f"  X {name}: {e}")

    SCHEMA_FILE.write_text(json.dumps(schema, indent=2))
    print(f"\nSchema atualizado em {SCHEMA_FILE}")
    print(f"Compromisso resolvido para: {schema['types'].get('Compromisso', '(nao criado)')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
