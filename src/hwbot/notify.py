from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from hwbot.db import Database
from hwbot.formatting import format_homework_card
from hwbot.models import Homework, Submission
from hwbot.reminders import collect_reminder_targets, reminder_text
from hwbot.timeutil import now_ts

logger = logging.getLogger(__name__)


async def broadcast_homework(bot: Bot, db: Database, homework: Homework) -> int:
    students = await db.students_in_groups(homework.group_codes)
    sent = 0
    moment = now_ts()
    text = "Новое ДЗ\n\n" + format_homework_card(homework, None, moment)
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


async def send_due_reminders(bot: Bot, db: Database) -> int:
    now = now_ts()
    homeworks = await db.list_homeworks(active_only=True)
    students = await db.list_students()
    latest: dict[tuple[int, int], Submission] = {}
    for student in students:
        for homework in homeworks:
            submission = await db.latest_submission(student.id, homework.id)
            if submission is not None:
                latest[(homework.id, student.id)] = submission
    sent_keys = await db.sent_reminder_keys()
    targets = collect_reminder_targets(homeworks, students, latest, sent_keys, now)
    sent = 0
    for target in targets:
        telegram_id = target.student.telegram_id
        if telegram_id is None:
            continue
        try:
            await bot.send_message(telegram_id, reminder_text(target))
        except TelegramAPIError:
            logger.warning("reminder failed for student_id=%s", target.student.id)
            continue
        await db.mark_reminder_sent(target.homework.id, target.student.id, target.window)
        sent += 1
    return sent
