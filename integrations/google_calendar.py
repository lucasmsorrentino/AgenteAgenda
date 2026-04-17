"""Google Calendar API integration.

Handles OAuth2 authentication, event fetching, and task management
using the [TODO] prefix convention for to-do items.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

from config.settings import GOOGLE_CALENDAR_ID, GOOGLE_CREDENTIALS_PATH, GOOGLE_TOKEN_PATH, TIMEZONE
from models.schemas import Event, Task, TaskSource, TaskStatus

TODO_PREFIX = "[TODO]"
DONE_PREFIX = "[DONE]"


class GoogleCalendarClient:
    """Wrapper around Google Calendar API v3."""

    def __init__(
        self,
        credentials_path: str = GOOGLE_CREDENTIALS_PATH,
        token_path: str = GOOGLE_TOKEN_PATH,
        calendar_id: str = GOOGLE_CALENDAR_ID,
    ):
        self.credentials_path = Path(credentials_path)
        self.token_path = Path(token_path)
        self.calendar_id = calendar_id
        self.service = None
        self.tz = ZoneInfo(TIMEZONE)

    def authenticate(self) -> None:
        """Build credentials from token.json, refresh if expired."""
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        SCOPES = ["https://www.googleapis.com/auth/calendar"]
        creds = None

        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Refreshing Google Calendar token...")
                creds.refresh(Request())
            else:
                if not self.credentials_path.exists():
                    raise FileNotFoundError(
                        f"Google credentials not found at {self.credentials_path}. "
                        "Download from Google Cloud Console."
                    )
                logger.info("Starting Google OAuth2 flow...")
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_path), SCOPES
                )
                creds = flow.run_local_server(port=0)

            # Save token for next run
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(creds.to_json())
            logger.info("Google Calendar token saved to {}", self.token_path)

        self.service = build("calendar", "v3", credentials=creds)
        logger.info("Google Calendar authenticated successfully")

    def _parse_event(self, item: dict) -> Event:
        """Parse a Google Calendar API event item into our Event model."""
        start_raw = item.get("start", {})
        end_raw = item.get("end", {})

        is_all_day = "date" in start_raw and "dateTime" not in start_raw

        if is_all_day:
            start = datetime.strptime(start_raw["date"], "%Y-%m-%d").replace(tzinfo=self.tz)
            end = datetime.strptime(end_raw["date"], "%Y-%m-%d").replace(tzinfo=self.tz)
        else:
            start = datetime.fromisoformat(start_raw["dateTime"])
            end = datetime.fromisoformat(end_raw["dateTime"])

        title = item.get("summary", "(sem titulo)")
        is_todo = title.startswith(TODO_PREFIX) or title.startswith(DONE_PREFIX)
        is_done = title.startswith(DONE_PREFIX)
        # Clean prefix for display
        display_title = title
        for prefix in (TODO_PREFIX, DONE_PREFIX):
            if display_title.startswith(prefix):
                display_title = display_title[len(prefix) :].strip()

        return Event(
            id=item["id"],
            title=display_title,
            start=start,
            end=end,
            description=item.get("description", ""),
            location=item.get("location", ""),
            is_all_day=is_all_day,
            is_todo=is_todo,
            is_done=is_done,
            updated=item.get("updated", ""),
            recurring_event_id=item.get("recurringEventId"),
        )

    def get_today_events(self) -> list[Event]:
        """Fetch all events for today."""
        now = datetime.now(self.tz)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timedelta(days=1)

        return self._fetch_events(start_of_day, end_of_day)

    def get_upcoming_events(self, minutes: int = 60) -> list[Event]:
        """Fetch events starting within the next N minutes."""
        now = datetime.now(self.tz)
        future = now + timedelta(minutes=minutes)
        return self._fetch_events(now, future)

    def get_upcoming_todos(self, days: int = 60) -> list[Event]:
        """Fetch pending [TODO] events in the next N days (excludes [DONE])."""
        now = datetime.now(self.tz)
        future = now + timedelta(days=days)
        events = self._fetch_events(now, future)
        return [e for e in events if e.is_todo and not e.is_done]

    def get_all_events_range(self, days_back: int = 7, days_forward: int = 60) -> list[Event]:
        """Fetch all events in a range (for syncing to Anytype)."""
        now = datetime.now(self.tz)
        start = now - timedelta(days=days_back)
        end = now + timedelta(days=days_forward)
        return self._fetch_events(start, end)

    def _fetch_events(self, time_min: datetime, time_max: datetime) -> list[Event]:
        """Fetch events in a time range from Google Calendar."""
        if not self.service:
            raise RuntimeError("Not authenticated. Call authenticate() first.")

        try:
            result = (
                self.service.events()
                .list(
                    calendarId=self.calendar_id,
                    timeMin=time_min.isoformat(),
                    timeMax=time_max.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            items = result.get("items", [])
            return [self._parse_event(item) for item in items]
        except Exception as e:
            logger.error("Failed to fetch calendar events: {}", e)
            return []

    def get_todos(self) -> list[Task]:
        """Get pending [TODO] events from today (excludes [DONE])."""
        events = self.get_today_events()
        todos = []
        for ev in events:
            if ev.is_todo and not ev.is_done:
                todos.append(
                    Task(
                        id=ev.id,
                        title=ev.title,
                        due=ev.start,
                        status=TaskStatus.PENDING,
                        source=TaskSource.CALENDAR,
                        calendar_event_id=ev.id,
                    )
                )
        return todos

    def create_todo(self, title: str, due: datetime | None = None) -> str:
        """Create a [TODO] event in Google Calendar. Returns event ID."""
        if not self.service:
            raise RuntimeError("Not authenticated. Call authenticate() first.")

        now = datetime.now(self.tz)
        start = due or now.replace(minute=0, second=0) + timedelta(hours=1)
        end = start + timedelta(minutes=30)

        event_body = {
            "summary": f"{TODO_PREFIX} {title}",
            "start": {"dateTime": start.isoformat(), "timeZone": TIMEZONE},
            "end": {"dateTime": end.isoformat(), "timeZone": TIMEZONE},
            "description": f"Created via Telegram bot at {now.strftime('%H:%M')}",
        }

        try:
            event = (
                self.service.events()
                .insert(calendarId=self.calendar_id, body=event_body)
                .execute()
            )
            event_id = event["id"]
            logger.info("Created TODO event: {} ({})", title, event_id)
            return event_id
        except Exception as e:
            logger.error("Failed to create TODO event: {}", e)
            raise

    def create_event(
        self,
        title: str,
        start: datetime,
        end: datetime,
        description: str = "",
        location: str = "",
        recurrence: list[str] | None = None,
    ) -> str:
        """Create a regular calendar event (not a [TODO]).

        recurrence: list of RRULE strings, e.g. ["RRULE:FREQ=WEEKLY;BYDAY=MO,WE"].
        Returns the new event ID.
        """
        if not self.service:
            raise RuntimeError("Not authenticated. Call authenticate() first.")

        body = {
            "summary": title,
            "start": {"dateTime": start.isoformat(), "timeZone": TIMEZONE},
            "end": {"dateTime": end.isoformat(), "timeZone": TIMEZONE},
        }
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        if recurrence:
            body["recurrence"] = recurrence

        event = (
            self.service.events()
            .insert(calendarId=self.calendar_id, body=body)
            .execute()
        )
        logger.info("Created event: {} ({})", title, event["id"])
        return event["id"]

    def get_event_raw(self, event_id: str) -> dict:
        """Fetch raw event payload (needed for editing recurring series)."""
        if not self.service:
            raise RuntimeError("Not authenticated. Call authenticate() first.")
        return (
            self.service.events()
            .get(calendarId=self.calendar_id, eventId=event_id)
            .execute()
        )

    def update_event(
        self,
        event_id: str,
        scope: str = "single",
        **fields,
    ) -> str:
        """Update an event.

        scope:
          - "single": update only this instance (works for both standalone and
            recurring instances; Google distinguishes via the eventId form).
          - "all": update the whole series. Resolves the master via
            recurringEventId when needed.

        fields supported: title, start, end, description, location, recurrence.
        Returns the updated event ID (may differ from input when scope="all").
        """
        if not self.service:
            raise RuntimeError("Not authenticated. Call authenticate() first.")

        target_id = event_id
        if scope == "all":
            current = self.get_event_raw(event_id)
            target_id = current.get("recurringEventId") or event_id

        event = (
            self.service.events()
            .get(calendarId=self.calendar_id, eventId=target_id)
            .execute()
        )

        if "title" in fields:
            event["summary"] = fields["title"]
        if "description" in fields:
            event["description"] = fields["description"]
        if "location" in fields:
            event["location"] = fields["location"]
        if "start" in fields:
            event["start"] = {"dateTime": fields["start"].isoformat(), "timeZone": TIMEZONE}
        if "end" in fields:
            event["end"] = {"dateTime": fields["end"].isoformat(), "timeZone": TIMEZONE}
        if "recurrence" in fields:
            event["recurrence"] = fields["recurrence"]

        self.service.events().update(
            calendarId=self.calendar_id, eventId=target_id, body=event
        ).execute()
        logger.info("Updated event {} (scope={})", target_id, scope)
        return target_id

    def delete_event(self, event_id: str, scope: str = "single") -> str:
        """Delete an event.

        scope="single": cancel just this instance.
        scope="all":    cancel the whole recurring series.
        Returns the ID that was deleted.
        """
        if not self.service:
            raise RuntimeError("Not authenticated. Call authenticate() first.")

        target_id = event_id
        if scope == "all":
            current = self.get_event_raw(event_id)
            target_id = current.get("recurringEventId") or event_id

        self.service.events().delete(
            calendarId=self.calendar_id, eventId=target_id
        ).execute()
        logger.info("Deleted event {} (scope={})", target_id, scope)
        return target_id

    def mark_todo_done(self, event_id: str) -> None:
        """Mark a [TODO] event as [DONE] by updating its title."""
        if not self.service:
            raise RuntimeError("Not authenticated. Call authenticate() first.")

        try:
            event = (
                self.service.events()
                .get(calendarId=self.calendar_id, eventId=event_id)
                .execute()
            )
            title = event.get("summary", "")
            if title.startswith(TODO_PREFIX):
                title = DONE_PREFIX + title[len(TODO_PREFIX) :]
            event["summary"] = title
            event["colorId"] = "2"  # Green-ish

            self.service.events().update(
                calendarId=self.calendar_id, eventId=event_id, body=event
            ).execute()
            logger.info("Marked event as done: {}", event_id)
        except Exception as e:
            logger.error("Failed to mark event as done: {}", e)

    def snooze_todo(self, event_id: str, minutes: int = 30) -> None:
        """Reschedule a [TODO] event by N minutes."""
        if not self.service:
            raise RuntimeError("Not authenticated. Call authenticate() first.")

        try:
            event = (
                self.service.events()
                .get(calendarId=self.calendar_id, eventId=event_id)
                .execute()
            )
            start = datetime.fromisoformat(event["start"]["dateTime"])
            end = datetime.fromisoformat(event["end"]["dateTime"])

            new_start = start + timedelta(minutes=minutes)
            new_end = end + timedelta(minutes=minutes)

            event["start"]["dateTime"] = new_start.isoformat()
            event["end"]["dateTime"] = new_end.isoformat()

            self.service.events().update(
                calendarId=self.calendar_id, eventId=event_id, body=event
            ).execute()
            logger.info("Snoozed event {} by {} min", event_id, minutes)
        except Exception as e:
            logger.error("Failed to snooze event: {}", e)
