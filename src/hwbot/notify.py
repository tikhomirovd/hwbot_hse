from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from aiogram.exceptions import TelegramAPIError

from hwbot.config import Settings
from hwbot.course import DEFAULT_COURSE_PATH, Course, load_course
from hwbot.db import Database
from hwbot.formatting import (
    admin_submission_notice,
    new_homework_announcement,
    team_reminder_text,
)
from hwbot.grading import days_late
from hwbot.models import Assessment, ReminderTarget, Student, Submission
from hwbot.reminders import (
    collect_reminder_targets,
    parse_late_days,
    reminder_text,
)
from hwbot.timeutil import now_ts

logger = logging.getLogger(__name__)


class MessageSender(Protocol):
    async def send_message(self, chat_id: int, text: str) -> object: ...


async def broadcast_text(
    bot: MessageSender,
    db: Database,
    text: str,
    *,
    group_codes: Sequence[str] | None = None,
) -> int:
    students = (
        await db.students_in_groups(group_codes)
        if group_codes is not None
        else await db.list_students()
    )
    return await send_to_students(bot, students, text)


async def send_to_students(
    bot: MessageSender, students: Sequence[Student], text: str
) -> int:
    """Разослать текст тем, кто зашёл в бота. Ошибка у одного не мешает остальным."""
    sent = 0
    for student in students:
        if student.telegram_id is None:
            continue
        try:
            await bot.send_message(student.telegram_id, text)
        except TelegramAPIError:
            logger.warning("send failed for student_id=%s", student.id)
            continue
        sent += 1
    return sent


async def broadcast_homework(bot: MessageSender, db: Database, assessment: Assessment) -> int:
    return await broadcast_text(bot, db, new_homework_announcement(assessment))


async def notify_admins_submission(
    bot: MessageSender,
    settings: Settings,
    student: Student,
    assessment: Assessment,
    submission: Submission,
    *,
    previous: Submission | None,
) -> int:
    text = admin_submission_notice(
        student, assessment, submission, previous=previous
    )
    sent = 0
    for admin_id in sorted(settings.admin_telegram_ids):
        try:
            await bot.send_message(admin_id, text)
        except TelegramAPIError:
            logger.warning("admin submission notice failed for chat_id=%s", admin_id)
            continue
        sent += 1
    return sent


async def notify_admins_latest_submissions(
    bot: MessageSender,
    settings: Settings,
    db: Database,
    *,
    assessment_code: str | None = None,
) -> int:
    assessments = {
        item.id: item
        for item in await db.list_assessments(submit_via_bot=True)
        if assessment_code is None or item.code == assessment_code
    }
    students = {item.id: item for item in await db.list_students()}
    latest = await db.latest_submissions_map()
    ordered = sorted(latest.values(), key=lambda item: (item.submitted_at, item.id))
    sent = 0
    for submission in ordered:
        assessment = assessments.get(submission.assessment_id)
        student = students.get(submission.student_id)
        if assessment is None or student is None:
            continue
        sent += await notify_admins_submission(
            bot, settings, student, assessment, submission, previous=None
        )
    return sent


def _days_for_target(target: ReminderTarget, now: int) -> int | None:
    days = parse_late_days(target.window)
    if days is None and target.assessment.deadline_ts is not None:
        days = days_late(now, target.assessment.deadline_ts)
    return days


async def send_due_reminders(
    bot: MessageSender,
    db: Database,
    *,
    now: int | None = None,
    course: Course | None = None,
) -> int:
    moment = now_ts() if now is None else now
    loaded = course if course is not None else load_course(DEFAULT_COURSE_PATH)
    assessments = await db.list_assessments(submit_via_bot=True)
    students = await db.list_students()
    latest = await db.latest_submissions_map()
    sent_keys = await db.sent_reminder_keys()
    targets = collect_reminder_targets(assessments, students, latest, sent_keys, moment)
    sent = 0
    for target in targets:
        telegram_id = target.student.telegram_id
        if telegram_id is None:
            continue
        text = reminder_text(
            target,
            days=_days_for_target(target, moment),
            now_ts=moment,
            course=loaded,
        )
        try:
            await bot.send_message(telegram_id, text)
        except TelegramAPIError:
            logger.warning("reminder failed for student_id=%s", target.student.id)
            continue
        await db.mark_reminder_sent(target.assessment.id, target.student.id, target.window)
        sent += 1
    return sent + await send_team_reminders(bot, db, now=moment, course=loaded)


TEAM_WINDOW = "team_lock"


async def send_team_reminders(
    bot: MessageSender, db: Database, *, now: int, course: Course
) -> int:
    """Одно напоминание до закрытия сборки каждому, кто ещё без команды.

    Повторы отсекает reminders_sent — по строке экзамена: внешний ключ
    требует работу, а напоминание про команду к экзамену и относится.
    """
    teams = course.teams
    if teams.remind_at_ts is None or not teams.remind_at_ts <= now <= teams.lock_at_ts:
        return 0
    exam = next((item for item in await db.list_assessments() if item.code == "exam"), None)
    if exam is None:
        return 0
    in_team = {member.id for team in await db.list_teams() for member in team.members}
    done = await db.sent_reminder_keys()
    sent = 0
    for student in await db.list_students():
        if student.telegram_id is None or student.id in in_team:
            continue
        if (exam.id, student.id, TEAM_WINDOW) in done:
            continue
        try:
            await bot.send_message(student.telegram_id, team_reminder_text(teams))
        except TelegramAPIError:
            logger.warning("team reminder failed for student_id=%s", student.id)
            continue
        await db.mark_reminder_sent(exam.id, student.id, TEAM_WINDOW, now)
        sent += 1
    return sent
