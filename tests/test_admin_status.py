from __future__ import annotations

from pathlib import Path
from typing import cast

from aiogram.filters import CommandObject
from aiogram.types import Message, User

from hwbot.config import Settings
from hwbot.course import DEFAULT_COURSE_PATH, load_course
from hwbot.db import Database
from hwbot.handlers.admin import cmd_export, cmd_status, resolve_homework_ref
from hwbot.ops import seed_course
from hwbot.roster import load_roster
from hwbot.timeutil import parse_deadline

ADMIN_ID = 77
NOW = parse_deadline("2026-09-17 20:05")


class FakeMessage:
    def __init__(self, user_id: int) -> None:
        self.from_user = User(id=user_id, is_bot=False, first_name="Кто-то")
        self.answers: list[str] = []
        self.documents: list[tuple[object, str | None]] = []

    async def answer(self, text: str, reply_markup: object = None) -> None:
        _ = reply_markup
        self.answers.append(text)

    async def answer_document(
        self, document: object, caption: str | None = None
    ) -> None:
        self.documents.append((document, caption))


def admin_settings() -> Settings:
    return Settings(
        bot_token="test",
        admin_telegram_ids=frozenset({ADMIN_ID}),
        db_path=Path("bot.db"),
        roster_path=Path("roster.csv"),
        timezone="Europe/Moscow",
    )


def _command(args: str | None) -> CommandObject:
    return CommandObject(prefix="/", command="status", args=args)


async def _seeded_course(db: Database, roster_path: Path) -> Database:
    await db.seed_roster(load_roster(roster_path))
    await seed_course(db, load_course(DEFAULT_COURSE_PATH))
    return db


async def test_status_command_is_admin_only(db: Database, roster_path: Path) -> None:
    await _seeded_course(db, roster_path)
    student = FakeMessage(ADMIN_ID + 1)
    await cmd_status(cast(Message, student), _command(None), db, admin_settings())
    assert student.answers == ["Это команда преподавателя."]


async def test_status_without_args_shows_board(db: Database, roster_path: Path) -> None:
    await _seeded_course(db, roster_path)
    students = await db.list_students()
    ivanov = next(item for item in students if "Иванов" in item.full_name)
    hw1 = await db.get_assessment_by_code("hw1")
    assert hw1 is not None
    await db.add_submission(ivanov.id, hw1.id, "https://github.com/x/hw", NOW)
    admin = FakeMessage(ADMIN_ID)
    await cmd_status(cast(Message, admin), _command(None), db, admin_settings())
    assert len(admin.answers) == 1
    text = admin.answers[0]
    assert "Сдали по работам" in text
    assert "ДЗ-1 ✓" in text
    assert "Иванов Иван Иванович (261)" in text
    assert "/status hw1" in text


async def test_status_hw1_matches_numeric_id(db: Database, roster_path: Path) -> None:
    await _seeded_course(db, roster_path)
    students = await db.list_students()
    ivanov = next(item for item in students if "Иванов" in item.full_name)
    hw1 = await db.get_assessment_by_code("hw1")
    assert hw1 is not None
    await db.add_submission(ivanov.id, hw1.id, "https://github.com/x/hw", NOW)
    by_code = FakeMessage(ADMIN_ID)
    by_id = FakeMessage(ADMIN_ID)
    await cmd_status(cast(Message, by_code), _command("hw1"), db, admin_settings())
    await cmd_status(
        cast(Message, by_id), _command(str(hw1.id)), db, admin_settings()
    )
    assert by_code.answers
    assert by_code.answers == by_id.answers
    text = by_code.answers[0]
    assert "Иванов Иван Иванович" in text
    assert "github.com/x/hw" in text
    assert "Петрова" in text
    resolved = await resolve_homework_ref(db, "HW1")
    assert resolved is not None
    assert resolved.id == hw1.id


async def test_export_caption_stays_short(db: Database, roster_path: Path) -> None:
    await _seeded_course(db, roster_path)
    students = await db.list_students()
    ivanov = next(item for item in students if "Иванов" in item.full_name)
    hw1 = await db.get_assessment_by_code("hw1")
    assert hw1 is not None
    await db.add_submission(ivanov.id, hw1.id, "https://github.com/x/hw", NOW)
    admin = FakeMessage(ADMIN_ID)
    await cmd_export(
        cast(Message, admin),
        CommandObject(prefix="/", command="export", args="hw1"),
        db,
        admin_settings(),
    )
    assert admin.documents
    _document, caption = admin.documents[0]
    assert caption is not None
    assert "Сдали: 1 /" in caption
    assert "https://github.com/x/hw" not in caption
    assert "github.com/x/hw" not in caption
