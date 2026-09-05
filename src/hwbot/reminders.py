from __future__ import annotations

from collections.abc import Sequence

from hwbot.grading import days_late
from hwbot.models import Assessment, ReminderTarget, Student, Submission
from hwbot.timeutil import format_dt

WINDOW_24H = "24h"
WINDOW_12H = "12h"
WINDOW_DEADLINE_PASSED = "deadline_passed"
WINDOW_ACCEPT_CLOSING = "accept_closing"
WINDOW_ACCEPT_CLOSED = "accept_closed"
WINDOW_LATE_PREFIX = "late_"
HOUR = 3600


def reminder_window(deadline_ts: int, now_ts: int) -> str | None:
    left = deadline_ts - now_ts
    if left <= 0:
        return None
    if left <= 12 * HOUR:
        return WINDOW_12H
    if left <= 24 * HOUR:
        return WINDOW_24H
    return None


def accept_closing_window(accept_until_ts: int, now_ts: int) -> bool:
    left = accept_until_ts - now_ts
    return 0 < left <= 24 * HOUR


def deadline_has_passed(deadline_ts: int, now_ts: int) -> bool:
    return now_ts >= deadline_ts


def accept_is_closed(accept_until_ts: int, now_ts: int) -> bool:
    return now_ts > accept_until_ts


def late_day_window(
    deadline_ts: int,
    accept_until_ts: int | None,
    now_ts: int,
    late_rule: str,
) -> str | None:
    if late_rule == "none":
        return None
    if now_ts <= deadline_ts:
        return None
    if accept_until_ts is not None and now_ts > accept_until_ts:
        return None
    days = days_late(now_ts, deadline_ts)
    if days < 2:
        return None
    return f"{WINDOW_LATE_PREFIX}{days}"


def parse_late_days(window: str) -> int | None:
    if not window.startswith(WINDOW_LATE_PREFIX):
        return None
    suffix = window.removeprefix(WINDOW_LATE_PREFIX)
    if not suffix.isdigit():
        return None
    return int(suffix)


def collect_reminder_targets(
    assessments: Sequence[Assessment],
    students: Sequence[Student],
    latest_by_pair: dict[tuple[int, int], Submission],
    sent: set[tuple[int, int, str]],
    now_ts: int,
) -> list[ReminderTarget]:
    registered = [student for student in students if student.telegram_id is not None]
    targets: list[ReminderTarget] = []
    for assessment in assessments:
        if not assessment.active or not assessment.submit_via_bot:
            continue
        windows: list[str] = []
        closed = (
            assessment.accept_until_ts is not None
            and accept_is_closed(assessment.accept_until_ts, now_ts)
        )
        if closed:
            windows.append(WINDOW_ACCEPT_CLOSED)
        elif assessment.deadline_ts is not None:
            before = reminder_window(assessment.deadline_ts, now_ts)
            if before is not None:
                windows.append(before)
            elif deadline_has_passed(assessment.deadline_ts, now_ts):
                windows.append(WINDOW_DEADLINE_PASSED)
                late = late_day_window(
                    assessment.deadline_ts,
                    assessment.accept_until_ts,
                    now_ts,
                    assessment.late_rule,
                )
                if late is not None:
                    windows.append(late)
        if (
            not closed
            and assessment.accept_until_ts is not None
            and accept_closing_window(assessment.accept_until_ts, now_ts)
        ):
            windows.append(WINDOW_ACCEPT_CLOSING)
        if not windows:
            continue
        for student in registered:
            if (assessment.id, student.id) in latest_by_pair:
                continue
            for window in windows:
                key = (assessment.id, student.id, window)
                if key in sent:
                    continue
                targets.append(
                    ReminderTarget(
                        assessment=assessment, student=student, window=window
                    )
                )
    return targets


def _work_name(assessment: Assessment) -> str:
    return assessment.label or assessment.title


def _format_cap(cap: float) -> str:
    if cap == int(cap):
        return str(int(cap))
    return str(cap)


def reminder_text(target: ReminderTarget, cap: float | None = None) -> str:
    title = _work_name(target.assessment)
    accept = target.assessment.accept_until_ts
    accept_text = format_dt(accept) if accept is not None else "закрытия приёма"
    if target.window == WINDOW_24H:
        return (
            f"Завтра дедлайн по «{title}». Ты ещё не сдал.\n"
            "После дедлайна приём ещё откроется на неделю, но каждый день минус балл.\n"
            "Сдать: /submit"
        )
    if target.window == WINDOW_12H:
        return (
            f"Осталось 12 часов на «{title}». Ты ещё не сдал.\n"
            "Потом начнётся просрочка: каждые сутки минус балл.\n"
            "Сдать: /submit"
        )
    if target.window == WINDOW_DEADLINE_PASSED:
        extra = ""
        if target.assessment.late_rule == "homework":
            extra = " каждые сутки минус балл, ниже 4 не опустимся."
        elif target.assessment.late_rule == "project1":
            extra = " каждые сутки минус балл."
        return (
            f"Дедлайн по «{title}» прошёл, приём открыт до {accept_text}."
            f"{extra}\n"
            "Сдать: /submit"
        )
    if target.window == WINDOW_ACCEPT_CLOSED:
        return f"Всё, сдать «{title}» больше нельзя, будет 0 баллов."
    late_days = parse_late_days(target.window)
    if late_days is not None:
        cap_part = ""
        if cap is not None:
            cap_part = f" Сейчас потолок {_format_cap(cap)}."
        return (
            f"Хоп, по «{title}» съелся ещё минус один балл.{cap_part}\n"
            "Очень жду сдачу.\n"
            "Сдать: /submit"
        )
    return (
        f"Завтра приём по «{title}» закроется совсем, дальше 0.\n"
        "Сдать: /submit"
    )
