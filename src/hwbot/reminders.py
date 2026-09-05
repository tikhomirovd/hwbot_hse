from __future__ import annotations

from collections.abc import Sequence

from hwbot.course import Course, LateRule
from hwbot.errors import CourseError
from hwbot.formatting import format_cap, work_name
from hwbot.grading import days_late, late_cap
from hwbot.models import Assessment, ReminderTarget, Student, Submission
from hwbot.telegramutil import escape_html
from hwbot.timeutil import format_human_dt, is_quiet_hours

WINDOW_24H = "24h"
WINDOW_12H = "12h"
WINDOW_DEADLINE_PASSED = "deadline_passed"
WINDOW_ACCEPT_CLOSING = "accept_closing"
WINDOW_ACCEPT_CLOSED = "accept_closed"
WINDOW_LATE_PREFIX = "late_"
HOUR = 3600


def reached_windows(
    deadline_ts: int | None,
    now_ts: int,
    *,
    accept_until_ts: int | None = None,
    late_rule: str = "none",
) -> list[str]:
    windows: list[str] = []
    closed = accept_until_ts is not None and now_ts > accept_until_ts
    if deadline_ts is not None:
        if now_ts >= deadline_ts - 24 * HOUR:
            windows.append(WINDOW_24H)
        if now_ts >= deadline_ts - 12 * HOUR:
            windows.append(WINDOW_12H)
        if now_ts >= deadline_ts:
            windows.append(WINDOW_DEADLINE_PASSED)
            if late_rule != "none" and not closed:
                days = days_late(now_ts, deadline_ts)
                for day in range(2, days + 1):
                    windows.append(f"{WINDOW_LATE_PREFIX}{day}")
    if accept_until_ts is not None and now_ts >= accept_until_ts - 24 * HOUR:
        if not closed:
            windows.append(WINDOW_ACCEPT_CLOSING)
    if closed:
        windows.append(WINDOW_ACCEPT_CLOSED)
    return windows


def parse_late_days(window: str) -> int | None:
    if not window.startswith(WINDOW_LATE_PREFIX):
        return None
    suffix = window.removeprefix(WINDOW_LATE_PREFIX)
    if not suffix.isdigit():
        return None
    return int(suffix)


def _registered_after_close(student: Student, assessment: Assessment) -> bool:
    accept = assessment.accept_until_ts
    registered = student.registered_at
    if accept is None or registered is None:
        return False
    return registered > accept


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
        windows = reached_windows(
            assessment.deadline_ts,
            now_ts,
            accept_until_ts=assessment.accept_until_ts,
            late_rule=assessment.late_rule,
        )
        if not windows:
            continue
        for student in registered:
            if _registered_after_close(student, assessment):
                continue
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


def _rule(course: Course | None, name: str) -> LateRule | None:
    if course is None:
        return None
    try:
        return course.late_rule_named(name)
    except CourseError:
        return None


def _cap_now(assessment: Assessment, now_ts: int, course: Course | None) -> float | None:
    if assessment.deadline_ts is None or now_ts <= assessment.deadline_ts:
        return None
    rule = _rule(course, assessment.late_rule)
    if rule is None:
        return None
    return late_cap(rule, days_late(now_ts, assessment.deadline_ts))


def reminder_text(
    target: ReminderTarget,
    cap: float | None = None,
    *,
    now_ts: int | None = None,
    course: Course | None = None,
) -> str:
    title = escape_html(work_name(target.assessment))
    accept = target.assessment.accept_until_ts
    accept_text = format_human_dt(accept) if accept is not None else "закрытия приёма"
    deadline = target.assessment.deadline_ts
    deadline_text = format_human_dt(deadline) if deadline is not None else "дедлайна"
    resolved_cap = cap
    if resolved_cap is None and now_ts is not None:
        resolved_cap = _cap_now(target.assessment, now_ts, course)
    cap_html = (
        f"<b>{format_cap(resolved_cap)} из 10</b>" if resolved_cap is not None else None
    )
    if target.window == WINDOW_24H:
        return (
            f"⏳ Завтра дедлайн по <b>{title}</b>, а от тебя пока тихо.\n\n"
            f"Сдать надо до {deadline_text}. После этого приём ещё неделю открыт, "
            "но каждые начатые сутки опускают потолок на 1 балл.\n\n"
            "📤 /submit"
        )
    if target.window == WINDOW_12H:
        clock = deadline_text.split(", ")[-1] if ", " in deadline_text else "23:59"
        return (
            f"⏳ 12 часов до дедлайна по <b>{title}</b>.\n\n"
            f"Ещё успеваешь на полный балл. После {clock} начнётся просрочка.\n\n"
            "📤 /submit"
        )
    if target.window == WINDOW_DEADLINE_PASSED:
        cap_line = cap_html or "<b>9 из 10</b>"
        return (
            f"Дедлайн по <b>{title}</b> прошёл — но это ещё не конец.\n\n"
            f"Приём открыт до {accept_text}. Потолок сейчас {cap_line} "
            "и опускается на 1 каждые сутки. Ниже 4 в эту неделю не упадёт.\n\n"
            "📤 /submit"
        )
    if target.window == WINDOW_ACCEPT_CLOSED:
        return (
            f"Приём по <b>{title}</b> закрыт, за неё стоит 0.\n\n"
            "Это не приговор для курса: домашние задания весят 25% накопленной, "
            "и это одна работа из четырёх. Дальше есть где отыграть.\n\n"
            "📊 Посмотреть, как это сказалось: /grade"
        )
    if target.window == WINDOW_ACCEPT_CLOSING:
        floor = "4 из 10"
        if resolved_cap is not None:
            floor = f"{format_cap(resolved_cap)} из 10"
        return (
            f"⚠️ Завтра приём по <b>{title}</b> закроется совсем.\n\n"
            f"После {accept_text} принять уже не смогу — за работу встанет 0. "
            f"Потолок сейчас {floor}, и это сильно лучше нуля.\n\n"
            "📤 /submit"
        )
    late_days = parse_late_days(target.window)
    if late_days is not None:
        rule = _rule(course, target.assessment.late_rule)
        at_floor = (
            resolved_cap is not None
            and rule is not None
            and resolved_cap <= rule.floor
            and rule.floor > 0
        )
        shown = cap_html or "<b>4 из 10</b>"
        if at_floor:
            return (
                f"📉 По <b>{title}</b> потолок дошёл до {shown} — ниже он в эту неделю "
                "уже не опустится.\n\n"
                f"Но {accept_text} приём закроется совсем, и тогда будет 0. Время ещё есть.\n\n"
                "📤 /submit"
            )
        return (
            f"📉 По <b>{title}</b> прошли ещё сутки — потолок теперь {shown}.\n\n"
            f"Приём открыт до {format_human_day_safe(accept)}. "
            "Чем раньше пришлёшь, тем больше останется.\n\n"
            "📤 /submit"
        )
    return (
        f"Завтра приём по <b>{title}</b> закроется совсем, дальше 0.\n\n"
        "📤 /submit"
    )


def format_human_day_safe(ts: int | None) -> str:
    if ts is None:
        return "закрытия приёма"
    return format_human_dt(ts)


def reminders_are_quiet(now_ts: int) -> bool:
    return is_quiet_hours(now_ts)
