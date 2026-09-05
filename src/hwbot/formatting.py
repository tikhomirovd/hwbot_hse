from __future__ import annotations

from hwbot.models import Assessment, Student, Submission
from hwbot.timeutil import format_dt, format_remaining, is_deadline_open


def homework_status_for_student(
    assessment: Assessment,
    submission: Submission | None,
    now_ts: int,
) -> str:
    close_ts = assessment.accept_until_ts
    deadline_ts = assessment.deadline_ts
    if submission is not None:
        if (
            deadline_ts is not None
            and submission.submitted_at > deadline_ts
            and close_ts is not None
            and submission.submitted_at <= close_ts
        ):
            return "сдано с опозданием"
        return "сдано"
    if close_ts is not None and is_deadline_open(close_ts, now_ts):
        if deadline_ts is not None and now_ts > deadline_ts:
            return f"просрочено · принимается до {format_dt(close_ts)}"
        return "не сдано"
    if close_ts is not None:
        return "приём закрыт · 0"
    if deadline_ts is not None and is_deadline_open(deadline_ts, now_ts):
        return "не сдано"
    return "приём закрыт · 0"


def format_homework_card(
    assessment: Assessment,
    submission: Submission | None,
    now_ts: int,
) -> str:
    status = homework_status_for_student(assessment, submission, now_ts)
    lines = [
        f"#{assessment.id} {assessment.label} · {assessment.title}",
    ]
    if assessment.deadline_ts is not None:
        lines.append(
            f"Дедлайн: {format_dt(assessment.deadline_ts)} "
            f"({format_remaining(assessment.deadline_ts, now_ts)})"
        )
    if assessment.accept_until_ts is not None:
        lines.append(f"Приём до: {format_dt(assessment.accept_until_ts)}")
    lines.extend(
        [
            f"Статус: {status}",
            "",
            assessment.body,
        ]
    )
    if submission is not None:
        lines.extend(["", f"Твоя сдача: {submission.payload}"])
    return "\n".join(lines)


def format_profile(student: Student) -> str:
    return (
        f"Ты {student.full_name}, группа {student.group_code}.\n\n"
        "/hw — активные ДЗ\n"
        "/submit — сдать ДЗ\n"
        "/mysubmissions — мои сдачи\n"
        "/grade — оценка\n"
        "/help — как пользоваться"
    )


def help_text() -> str:
    return (
        "Это бот для сдачи ДЗ и оценки.\n\n"
        "/start — регистрация или профиль\n"
        "/hw — какие работы сейчас принимаются\n"
        "/submit — отправить ссылку на GitHub или текст\n"
        "/mysubmissions — что уже сдал\n"
        "/grade — оценка и разбивка\n\n"
        "После дедлайна сдать ещё можно в течение недели: "
        "за каждые начатые сутки минус 1 балл, ниже 4 не опустимся. "
        "Позже приём закроется, будет 0. Пересдать можно, пока приём открыт."
    )


def format_hw_list(cards: list[str]) -> str:
    if not cards:
        return "Сейчас нет активных ДЗ."
    return "\n\n———\n\n".join(cards)
