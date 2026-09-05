from __future__ import annotations

from collections.abc import Sequence

from hwbot.models import Assessment, ReminderTarget, Student, Submission
from hwbot.timeutil import format_dt

WINDOW_24H = "24h"
WINDOW_12H = "12h"
WINDOW_DEADLINE_PASSED = "deadline_passed"
WINDOW_ACCEPT_CLOSING = "accept_closing"
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
        if assessment.deadline_ts is not None:
            before = reminder_window(assessment.deadline_ts, now_ts)
            if before is not None:
                windows.append(before)
            elif deadline_has_passed(assessment.deadline_ts, now_ts):
                still_open = (
                    assessment.accept_until_ts is None
                    or now_ts <= assessment.accept_until_ts
                )
                if still_open:
                    windows.append(WINDOW_DEADLINE_PASSED)
        if assessment.accept_until_ts is not None and accept_closing_window(
            assessment.accept_until_ts, now_ts
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


def reminder_text(target: ReminderTarget) -> str:
    title = target.assessment.title
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
    return (
        f"Завтра приём по «{title}» закроется совсем, дальше 0.\n"
        "Сдать: /submit"
    )
