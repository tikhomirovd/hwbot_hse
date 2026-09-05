from __future__ import annotations

from hwbot.course import DEFAULT_COURSE_PATH, load_course
from hwbot.db import Database
from hwbot.notify import send_due_reminders
from hwbot.ops import seed_course
from hwbot.roster import load_roster
from hwbot.config import PROJECT_ROOT
from hwbot.timeutil import parse_deadline


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


async def test_night_reminders_send_immediately(db: Database) -> None:
    await db.seed_roster(load_roster(PROJECT_ROOT / "data" / "roster.csv"))
    course = load_course(DEFAULT_COURSE_PATH)
    await seed_course(db, course)
    students = await db.list_students()
    student = next(item for item in students if "Абрамова" in item.full_name)
    await db.bind_telegram(student.id, 999, "abra")
    hw1 = await db.get_assessment_by_code("hw1")
    assert hw1 is not None
    assert hw1.deadline_ts is not None
    night = parse_deadline("2026-09-18 23:59")
    bot = FakeBot()
    sent = await send_due_reminders(bot, db, now=night, course=course)
    assert sent >= 1
    assert bot.sent
    second = FakeBot()
    assert await send_due_reminders(second, db, now=night, course=course) == 0
