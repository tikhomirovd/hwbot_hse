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
    seminar_group: str | None = None


@dataclass(frozen=True, slots=True)
class RosterRow:
    full_name: str
    group_code: str
    email: str
    seminar_group: str | None = None


@dataclass(frozen=True, slots=True)
class Assessment:
    id: int
    code: str
    label: str
    title: str
    body: str
    component: str
    weight_final: float
    submit_via_bot: bool
    issued_at: int | None
    deadline_ts: int | None
    accept_until_ts: int | None
    graded_on_ts: int | None
    late_rule: str
    blocking: bool
    active: bool


@dataclass(frozen=True, slots=True)
class Lesson:
    id: int
    code: str
    kind: str
    seminar_group: str | None
    topic: int
    title: str
    starts_ts: int
    ends_ts: int
    room: str | None
    module: int


@dataclass(frozen=True, slots=True)
class AttendanceMark:
    student_id: int
    lesson_id: int
    status: str
    marked_at: int


@dataclass(frozen=True, slots=True)
class Grade:
    student_id: int
    assessment_id: int
    score: float
    comment: str
    graded_at: int


@dataclass(frozen=True, slots=True)
class Submission:
    id: int
    student_id: int
    assessment_id: int
    payload: str
    submitted_at: int


@dataclass(frozen=True, slots=True)
class HomeworkStatusRow:
    student: Student
    submission: Submission | None
    accept_until_ts: int | None = None
    now_ts: int | None = None

    @property
    def status_label(self) -> str:
        if self.submission is not None:
            return "сдано"
        if (
            self.accept_until_ts is not None
            and self.now_ts is not None
            and self.now_ts > self.accept_until_ts
        ):
            return "не сдано (0)"
        return "не сдано"


@dataclass(frozen=True, slots=True)
class ReminderTarget:
    assessment: Assessment
    student: Student
    window: str
