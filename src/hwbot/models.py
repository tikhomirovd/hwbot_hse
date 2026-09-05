from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Student:
    id: int
    full_name: str
    group_code: str
    email: str
    telegram_id: int | None
    telegram_username: str | None


@dataclass(frozen=True, slots=True)
class RosterRow:
    full_name: str
    group_code: str
    email: str


@dataclass(frozen=True, slots=True)
class Homework:
    id: int
    title: str
    body: str
    deadline_ts: int
    group_codes: tuple[str, ...]
    active: bool
    created_at: int


@dataclass(frozen=True, slots=True)
class Submission:
    id: int
    student_id: int
    homework_id: int
    payload: str
    submitted_at: int


@dataclass(frozen=True, slots=True)
class HomeworkStatusRow:
    student: Student
    submission: Submission | None

    @property
    def status_label(self) -> str:
        return "сдано" if self.submission is not None else "не сдано (0)"


@dataclass(frozen=True, slots=True)
class ReminderTarget:
    homework: Homework
    student: Student
    window: str
