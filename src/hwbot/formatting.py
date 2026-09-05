from __future__ import annotations

from hwbot.models import Homework, Student, Submission
from hwbot.timeutil import format_dt, format_remaining, is_deadline_open


def homework_status_for_student(
    homework: Homework,
    submission: Submission | None,
    now_ts: int,
) -> str:
    open_now = is_deadline_open(homework.deadline_ts, now_ts)
    if submission is not None:
        return "сдано"
    if open_now:
        return "не сдано"
    return "закрыто · 0"


def format_homework_card(
    homework: Homework,
    submission: Submission | None,
    now_ts: int,
) -> str:
    status = homework_status_for_student(homework, submission, now_ts)
    groups = ", ".join(homework.group_codes)
    lines = [
        f"#{homework.id} {homework.title}",
        f"Группы: {groups}",
        f"Дедлайн: {format_dt(homework.deadline_ts)} ({format_remaining(homework.deadline_ts, now_ts)})",
        f"Статус: {status}",
        "",
        homework.body,
    ]
    if submission is not None:
        lines.extend(["", f"Твоя сдача: {submission.payload}"])
    return "\n".join(lines)


def format_profile(student: Student) -> str:
    return (
        f"Ты {student.full_name}, группа {student.group_code}.\n\n"
        "/hw — активные ДЗ\n"
        "/submit — сдать ДЗ\n"
        "/mysubmissions — мои сдачи\n"
        "/help — как пользоваться"
    )


def help_text() -> str:
    return (
        "Это бот для сдачи ДЗ.\n\n"
        "/start — регистрация или профиль\n"
        "/hw — какие ДЗ сейчас открыты\n"
        "/submit — отправить ссылку на GitHub или текст\n"
        "/mysubmissions — что уже сдал\n\n"
        "Дедлайн строгий: не успел — 0 баллов, после срока сдать нельзя.\n"
        "Пересдать можно только до дедлайна."
    )


def format_hw_list(cards: list[str]) -> str:
    if not cards:
        return "Сейчас нет активных ДЗ."
    return "\n\n———\n\n".join(cards)
