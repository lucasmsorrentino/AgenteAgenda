"""One-time setup: Anytype authentication + schema creation.

Authenticates with the Anytype local API and creates custom types
and properties for the productivity system.

Prerequisites:
    - Anytype desktop app must be running
    - Local API must be enabled (Settings → Local API)

Usage:
    cd productivity
    python scripts/setup_anytype.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from config.settings import ANYTYPE_API_VERSION, ANYTYPE_BASE_URL, ANYTYPE_SPACE_ID

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def authenticate(base_url: str) -> tuple[str, str]:
    """Run the 4-digit challenge flow to get an API key.

    Returns (api_key, space_id).
    """
    print("Starting Anytype authentication...")
    print("Make sure the Anytype app is open.\n")

    # Step 1: Create challenge
    resp = httpx.post(
        f"{base_url}/v1/auth/challenges",
        json={"app_name": "productivity_bot"},
        headers={"Anytype-Version": ANYTYPE_API_VERSION, "Content-Type": "application/json"},
    )
    resp.raise_for_status()
    challenge_id = resp.json()["challenge"]["id"]

    print(f"Challenge created (ID: {challenge_id})")
    print("👉 A 4-digit code appeared in the Anytype app.")
    code = input("Enter the 4-digit code: ").strip()

    # Step 2: Get API key
    resp = httpx.post(
        f"{base_url}/v1/auth/api_keys",
        json={"challenge_id": challenge_id, "code": code},
        headers={"Anytype-Version": ANYTYPE_API_VERSION, "Content-Type": "application/json"},
    )
    resp.raise_for_status()
    api_key = resp.json()["api_key"]

    # Step 3: List spaces
    resp = httpx.get(
        f"{base_url}/v1/spaces",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Anytype-Version": ANYTYPE_API_VERSION,
        },
    )
    resp.raise_for_status()
    spaces = resp.json().get("data", [])

    if not spaces:
        print("❌ No spaces found in Anytype!")
        sys.exit(1)

    if len(spaces) == 1:
        space = spaces[0]
    else:
        print("\nAvailable spaces:")
        for i, s in enumerate(spaces):
            print(f"  {i + 1}. {s.get('name', 'Unnamed')} ({s.get('id', '')})")
        idx = int(input("Select space number: ")) - 1
        space = spaces[idx]

    space_id = space.get("id", "")
    print(f"\n✅ Authenticated! Space: {space.get('name')} ({space_id})")

    # Save API key
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    key_file = DATA_DIR / "anytype_key.txt"
    key_file.write_text(api_key)
    print(f"   API key saved to: {key_file}")

    return api_key, space_id


def create_schema(base_url: str, api_key: str, space_id: str) -> dict:
    """Create custom types and properties for the productivity system."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Anytype-Version": ANYTYPE_API_VERSION,
        "Content-Type": "application/json",
    }

    def post(path: str, data: dict) -> dict:
        resp = httpx.post(f"{base_url}/v1/spaces/{space_id}/{path}", json=data, headers=headers)
        resp.raise_for_status()
        return resp.json()

    print("\nCreating schema...")
    schema = {}

    # --- Properties ---
    properties = [
        ("status", "select"),
        ("due_date", "date"),
        ("completed_at", "date"),
        ("source", "select"),
        ("calendar_event_id", "text"),
        ("recap_date", "date"),
        ("events_total", "number"),
        ("tasks_completed", "number"),
        ("tasks_missed", "number"),
        ("tags", "multi_select"),
        ("start", "date"),
        ("end", "date"),
        ("location", "text"),
        ("recurring", "checkbox"),
        ("classified_at", "date"),
        ("area", "select"),
        ("prioridade", "select"),
    ]

    schema["properties"] = {}
    for name, fmt in properties:
        try:
            result = post("properties", {"name": name, "format": fmt})
            prop = result.get("property", {})
            key = prop.get("key", "")
            schema["properties"][name] = key
            print(f"  ✅ Property: {name} ({fmt}) → {key}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 409:
                print(f"  ⚠️ Property '{name}' already exists — skipping")
            else:
                print(f"  ❌ Property '{name}' failed: {e}")

    # --- Types ---
    types = [
        ("Tarefa", "checkmark", "basic"),
        ("Resumo Diario", "calendar", "basic"),
        ("Nota Rapida", "memo", "basic"),
        ("Projeto", "folder", "basic"),
        ("Compromisso", "calendar", "basic"),
    ]

    schema["types"] = {}
    for name, icon, layout in types:
        try:
            result = post("types", {"name": name, "icon": icon, "layout": layout})
            type_data = result.get("type", {})
            key = type_data.get("key", "")
            schema["types"][name] = key
            print(f"  ✅ Type: {name} → {key}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 409:
                print(f"  ⚠️ Type '{name}' already exists — skipping")
            else:
                print(f"  ❌ Type '{name}' failed: {e}")

    # Save schema mapping
    schema_file = DATA_DIR / "anytype_schema.json"
    schema_file.write_text(json.dumps(schema, indent=2))
    print(f"\n📄 Schema mapping saved to: {schema_file}")

    return schema


def main():
    print("=" * 50)
    print("Anytype Setup")
    print("=" * 50)
    print()

    base_url = ANYTYPE_BASE_URL

    # Check if Anytype is reachable
    try:
        resp = httpx.get(f"{base_url}/v1/spaces", timeout=5)
        # Will fail with 401 if not authenticated — that's expected
    except httpx.ConnectError:
        print(f"❌ Cannot connect to Anytype at {base_url}")
        print("   Make sure:")
        print("   1. Anytype desktop app is running")
        print("   2. Local API is enabled (Settings → Local API)")
        return

    # Authenticate
    api_key, space_id = authenticate(base_url)

    # Create schema
    print()
    create_schema(base_url, api_key, space_id)

    print()
    print("=" * 50)
    print("Setup complete! Add these to your .env:")
    print(f"  ANYTYPE_API_KEY={api_key}")
    print(f"  ANYTYPE_SPACE_ID={space_id}")
    print("=" * 50)


if __name__ == "__main__":
    main()
