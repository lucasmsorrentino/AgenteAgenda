"""One-time setup: Google Calendar OAuth2 flow.

Guides you through authenticating with Google Calendar.

Prerequisites:
    1. Go to https://console.cloud.google.com
    2. Create a project, enable Google Calendar API
    3. Create OAuth2 Desktop App credentials
    4. Download credentials.json to productivity/data/

Usage:
    cd productivity
    python scripts/setup_google.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import GOOGLE_CREDENTIALS_PATH, GOOGLE_TOKEN_PATH


def main():
    creds_path = Path(GOOGLE_CREDENTIALS_PATH)
    token_path = Path(GOOGLE_TOKEN_PATH)

    print("=" * 50)
    print("Google Calendar Setup")
    print("=" * 50)
    print()

    if not creds_path.exists():
        print(f"❌ credentials.json not found at: {creds_path}")
        print()
        print("Steps to get it:")
        print("  1. Go to https://console.cloud.google.com")
        print("  2. Create a new project (or use existing)")
        print("  3. Enable 'Google Calendar API'")
        print("  4. Go to Credentials → Create OAuth 2.0 Client ID")
        print("  5. Choose 'Desktop app' as application type")
        print("  6. Download the JSON and save it as:")
        print(f"     {creds_path}")
        return

    if token_path.exists():
        print(f"✅ Token already exists at: {token_path}")
        answer = input("Re-authenticate? [y/N]: ").strip().lower()
        if answer != "y":
            print("Keeping existing token.")
            return
        token_path.unlink()

    print("Starting OAuth2 flow... A browser window will open.")
    print()

    from integrations.google_calendar import GoogleCalendarClient

    client = GoogleCalendarClient()
    client.authenticate()

    # Test by fetching today's events
    events = client.get_today_events()
    print()
    print(f"[OK] Authenticated! Found {len(events)} event(s) for today.")
    print(f"     Token saved to: {token_path}")


if __name__ == "__main__":
    main()
