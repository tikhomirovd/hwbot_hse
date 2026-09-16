from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from aiogram.exceptions import TelegramAPIError

from hwbot.course import DEFAULT_COURSE_PATH, Course, load_course
from hwbot.db import Database
from hwbot.formatting import new_homework_announcement
from hwbot.grading import days_late
from hwbot.models import Assessment, ReminderTarget
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
    sent = 0
    for student in students:
        if student.telegram_id is None:
            continue
        try:
            await bot.send_message(student.telegram_id, text)
        except TelegramAPIError:
            logger.warning("broadcast failed for student_id=%s", student.id)
            continue
        sent += 1
    return sent


async def broadcast_homework(bot: MessageSender, db: Database, assessment: Assessment) -> int:
    return await broadcast_text(bot, db, new_homework_announcement(assessment))


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
    return sent
