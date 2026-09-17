from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from aiogram.exceptions import TelegramAPIError

from hwbot.config import Settings
from hwbot.course import DEFAULT_COURSE_PATH, load_course
from hwbot.db import Database
from hwbot.models import Assessment, Student, Submission
from hwbot.notify import broadcast_text, notify_admins_submission, send_due_reminders
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
    night = parse_deadline("2026-09-26 23:59")  # ночь перед дедлайном hw1 (27.09)
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


class FlakyBot:
    def __init__(self, fail_ids: set[int]) -> None:
        self.fail_ids = fail_ids
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        if chat_id in self.fail_ids:
            raise TelegramAPIError(method=Mock(), message="fail")
        self.sent.append((chat_id, text))


def _notice_settings(*admin_ids: int) -> Settings:
    return Settings(
        bot_token="test",
        admin_telegram_ids=frozenset(admin_ids),
        db_path=Path("bot.db"),
        roster_path=Path("roster.csv"),
        timezone="Europe/Moscow",
    )


async def test_notify_admins_submission_sends_to_all_and_survives_one_failure() -> None:
    homework = Assessment(
        id=1,
        code="hw1",
        label="ДЗ-1",
        title="ДЗ 1",
        body="body",
        component="homework",
        weight_final=0.0625,
        submit_via_bot=True,
        issued_at=1,
        deadline_ts=2_000,
        accept_until_ts=2_000 + 7 * 86400,
        graded_on_ts=None,
        late_rule="homework",
        blocking=False,
        active=True,
    )
    student = Student(1, "Иванов Иван Иванович", "БАЦРФ261", "ivanov@example.edu", 1, "a")
    submission = Submission(1, 1, 1, "https://github.com/x/hw", 1_500)
    bot = FlakyBot(fail_ids={11})
    sent = await notify_admins_submission(
        bot,
        _notice_settings(22, 11),
        student,
        homework,
        submission,
        previous=None,
    )
    assert sent == 1
    assert [chat_id for chat_id, _text in bot.sent] == [22]
    assert "сдал" in bot.sent[0][1]
    assert "ДЗ-1" in bot.sent[0][1]
    assert "github.com/x/hw" in bot.sent[0][1]
