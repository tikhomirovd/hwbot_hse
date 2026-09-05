from __future__ import annotations

from collections.abc import Sequence

from hwbot.models import Homework, ReminderTarget, Student, Submission

WINDOW_24H = "24h"
WINDOW_12H = "12h"
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
    homeworks: Sequence[Homework],
    students: Sequence[Student],
    latest_by_pair: dict[tuple[int, int], Submission],
    sent: set[tuple[int, int, str]],
    now_ts: int,
) -> list[ReminderTarget]:
    by_group: dict[str, list[Student]] = {}
    for student in students:
        if student.telegram_id is None:
            continue
        by_group.setdefault(student.group_code, []).append(student)

    targets: list[ReminderTarget] = []
    for homework in homeworks:
        if not homework.active:
            continue
        window = reminder_window(homework.deadline_ts, now_ts)
        if window is None:
            continue
        for group in homework.group_codes:
            for student in by_group.get(group, []):
                if (homework.id, student.id) in latest_by_pair:
                    continue
                key = (homework.id, student.id, window)
                if key in sent:
                    continue
                targets.append(
                    ReminderTarget(homework=homework, student=student, window=window)
                )
    return targets


def reminder_text(target: ReminderTarget) -> str:
    title = target.homework.title
    if target.window == WINDOW_24H:
        return (
            f"Завтра дедлайн по «{title}». Ты ещё не сдал.\n"
            "После дедлайна сдать нельзя — будет 0.\n"
            "Сдать: /submit"
        )
    return (
        f"Осталось 12 часов на «{title}». Ты ещё не сдал.\n"
        "Потом задание закроется, будет 0.\n"
        "Сдать: /submit"
    )
