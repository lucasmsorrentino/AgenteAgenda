"""Pydantic models for the productivity system."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    DONE = "done"
    SKIPPED = "skipped"
    SNOOZED = "snoozed"


class TaskSource(str, Enum):
    CALENDAR = "calendar"
    TELEGRAM = "telegram"
    ANYTYPE = "anytype"
    MANUAL = "manual"


class Event(BaseModel):
    """A Google Calendar event."""

    id: str
    title: str
    start: datetime
    end: datetime
    description: str = ""
    location: str = ""
    is_all_day: bool = False
    is_todo: bool = False  # True if title starts with [TODO] or [DONE]
    is_done: bool = False  # True if title starts with [DONE]
    updated: str = ""  # Google's last-modified timestamp (RFC3339); used for diff in sync
    recurring_event_id: str | None = None  # set when this is an instance of a recurring series


class Task(BaseModel):
    """A task/to-do item."""

    id: str
    title: str
    due: datetime | None = None
    status: TaskStatus = TaskStatus.PENDING
    source: TaskSource = TaskSource.CALENDAR
    calendar_event_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)


class DailyRecap(BaseModel):
    """Summary of a day's activity."""

    date: str  # "2026-04-12"
    events_total: int = 0
    todos_completed: list[str] = Field(default_factory=list)
    todos_missed: list[str] = Field(default_factory=list)
    notes_created: int = 0
    suggestions: list[str] = Field(default_factory=list)


class ReminderAction(BaseModel):
    """An action taken on a reminder (done/skip/snooze)."""

    action: str  # "done" | "skip" | "snooze"
    task_id: str
    timestamp: datetime = Field(default_factory=datetime.now)
