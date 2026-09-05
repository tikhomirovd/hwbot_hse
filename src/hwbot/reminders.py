from __future__ import annotations

from collections.abc import Sequence

from hwbot.models import Assessment, ReminderTarget, Student, Submission

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
        if assessment.deadline_ts is None:
            continue
        window = reminder_window(assessment.deadline_ts, now_ts)
        if window is None:
            continue
        for student in registered:
            if (assessment.id, student.id) in latest_by_pair:
                continue
            key = (assessment.id, student.id, window)
            if key in sent:
                continue
            targets.append(
                ReminderTarget(assessment=assessment, student=student, window=window)
            )
    return targets


def reminder_text(target: ReminderTarget) -> str:
    title = target.assessment.title
    if target.window == WINDOW_24H:
        return (
            f"Завтра дедлайн по «{title}». Ты ещё не сдал.\n"
            "После дедлайна приём ещё откроется на неделю, но каждый день минус балл.\n"
            "Сдать: /submit"
        )
    return (
        f"Осталось 12 часов на «{title}». Ты ещё не сдал.\n"
        "Потом начнётся просрочка: каждые сутки минус балл.\n"
        "Сдать: /submit"
    )
