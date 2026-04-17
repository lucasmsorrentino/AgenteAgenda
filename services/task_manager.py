"""Task manager service.

Bridges Google Calendar and Anytype for task lifecycle management.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger

from config.settings import TIMEZONE
from models.schemas import Task, TaskSource, TaskStatus


class TaskManager:
    """Manages tasks across Google Calendar and Anytype."""

    def __init__(self, calendar_client=None, anytype_client=None):
        self.calendar = calendar_client
        self.anytype = anytype_client
        self.tz = ZoneInfo(TIMEZONE)

    async def add_task(self, title: str, due: datetime | None = None) -> Task:
        """Create a new task in Google Calendar (and optionally Anytype)."""
        event_id = ""
        if self.calendar:
            event_id = self.calendar.create_todo(title, due)

        task = Task(
            id=event_id or f"local_{int(datetime.now().timestamp())}",
            title=title,
            due=due,
            status=TaskStatus.PENDING,
            source=TaskSource.TELEGRAM,
            calendar_event_id=event_id or None,
        )

        # Also create in Anytype for tracking
        if self.anytype:
            props = {
                "status": "pending",
                "source": "telegram",
            }
            if due:
                props["due_date"] = due.isoformat()
            self.anytype.create_object(
                type_key="task",
                name=title,
                properties=props,
            )

        return task

    async def complete_task(self, task_id: str, title: str = "Tarefa") -> None:
        """Mark a task as done in Calendar and log in Anytype."""
        if self.calendar:
            self.calendar.mark_todo_done(task_id)

        if self.anytype:
            self.anytype.log_completed_task(title)

        logger.info("Task completed: {} ({})", title, task_id)

    async def skip_task(self, task_id: str) -> None:
        """Mark a task as skipped (no calendar change, just tracking)."""
        logger.info("Task skipped: {}", task_id)

    async def snooze_task(self, task_id: str, minutes: int = 30) -> None:
        """Reschedule a task by N minutes."""
        if self.calendar:
            self.calendar.snooze_todo(task_id, minutes)
        logger.info("Task snoozed: {} (+{} min)", task_id, minutes)

    async def get_pending_tasks(self) -> list[Task]:
        """Get all pending tasks from Calendar."""
        tasks = []
        if self.calendar:
            tasks = self.calendar.get_todos()
        return tasks
