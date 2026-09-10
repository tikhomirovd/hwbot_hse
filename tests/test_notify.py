from __future__ import annotations

from pathlib import Path

from hwbot.course import DEFAULT_COURSE_PATH, load_course
from hwbot.db import Database
from hwbot.notify import broadcast_text, send_due_reminders
from hwbot.ops import seed_course
from hwbot.roster import load_roster
from hwbot.timeutil import parse_deadline


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


async def test_night_reminders_send_immediately(db: Database, roster_path: Path) -> None:
    await db.seed_roster(load_roster(roster_path))
    course = load_course(DEFAULT_COURSE_PATH)
    await seed_course(db, course)
    students = await db.list_students()
    student = next(item for item in students if "Иванов" in item.full_name)
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


async def test_broadcast_text_filters_by_group(db: Database, roster_path: Path) -> None:
    await db.seed_roster(load_roster(roster_path))
    students = await db.list_students()
    student_261 = next(item for item in students if item.group_code == "БАЦРФ261")
    student_262 = next(item for item in students if item.group_code == "БАЦРФ262")
    await db.bind_telegram(student_261.id, 111, "a")
    await db.bind_telegram(student_262.id, 222, "b")
    bot = FakeBot()
    sent = await broadcast_text(bot, db, "опрос", group_codes=("БАЦРФ262",))
    assert sent == 1
    assert bot.sent == [(222, "опрос")]
