from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class MooError(Exception):
    code: str
    message: str
    exit_code: int
    details: dict[str, object] | None = None

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class TaskRef:
    list_id: str
    series_id: str
    task_id: str

    @classmethod
    def parse(cls, value: str) -> "TaskRef":
        parts = value.split(":")
        if len(parts) != 3 or any(
            not part.isascii() or not part.isdecimal() for part in parts
        ):
            raise MooError(
                "invalid_arguments", "Task reference must be LIST:SERIES:TASK IDs.", 2
            )
        return cls(*parts)

    def __str__(self) -> str:
        return f"{self.list_id}:{self.series_id}:{self.task_id}"


@dataclass(frozen=True)
class Note:
    id: str
    title: str
    text: str
    created: str | None = None
    modified: str | None = None


@dataclass(frozen=True)
class Task:
    ref: str
    list_id: str
    series_id: str
    task_id: str
    name: str
    tags: list[str] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    completed: str | None = None
    due: str | None = None
    priority: str | None = None
    recurring: bool = False


@dataclass(frozen=True)
class TaskList:
    id: str
    name: str
    smart: bool
    archived: bool


class Backend(Protocol):
    def lists(self) -> list[TaskList]: ...
    def tasks(
        self, list_id: str | None, tags: list[str], include_completed: bool
    ) -> list[Task]: ...
    def show(self, ref: TaskRef) -> Task: ...
    def add_note(self, ref: TaskRef, title: str, text: str) -> dict[str, object]: ...
    def change_tags(
        self, ref: TaskRef, tags: list[str], remove: bool
    ) -> dict[str, object]: ...
    def move(self, ref: TaskRef, destination: str) -> dict[str, object]: ...
    def auth_url(self) -> str: ...
    def exchange_token(self) -> str: ...
    def check_token(self) -> dict[str, object]: ...
