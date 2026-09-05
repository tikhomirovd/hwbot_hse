from __future__ import annotations

import pytest

from hwbot.db import Database
from hwbot.errors import DeadlineClosedError
from hwbot.roster import load_roster
from hwbot.config import PROJECT_ROOT


@pytest.fixture
async def seeded(db: Database) -> Database:
    await db.seed_roster(load_roster(PROJECT_ROOT / "data" / "roster.csv"))
    return db


async def test_seed_count(seeded: Database) -> None:
    assert await seeded.student_count() == 57


async def test_bind_and_submit_before_deadline(seeded: Database) -> None:
    students = await seeded.list_students()
    student = next(item for item in students if "Абрамова" in item.full_name)
    bound = await seeded.bind_telegram(student.id, 111, "abra")
    assert bound.telegram_id == 111
    homework = await seeded.create_homework(
        "ДЗ 1",
        "Ссылка на репо",
        deadline_ts=2_000_000_000,
        group_codes=("БАЦРФ261",),
        created_at=1_000,
    )
    submission = await seeded.add_submission(
        student.id, homework.id, "https://github.com/example/hw", submitted_at=1_500
    )
    assert submission.payload.startswith("https://github.com")
    latest = await seeded.latest_submission(student.id, homework.id)
    assert latest is not None
    assert latest.id == submission.id


async def test_reject_late_submission(seeded: Database) -> None:
    students = await seeded.list_students()
    student = next(item for item in students if item.group_code == "БАЦРФ261")
    homework = await seeded.create_homework(
        "ДЗ late",
        "text",
        deadline_ts=1_000,
        group_codes=("БАЦРФ261",),
        created_at=1,
    )
    with pytest.raises(DeadlineClosedError):
        await seeded.add_submission(student.id, homework.id, "https://github.com/x", submitted_at=1_001)


async def test_status_zero_for_missing(seeded: Database) -> None:
    homework = await seeded.create_homework(
        "ДЗ status",
        "text",
        deadline_ts=2_000_000_000,
        group_codes=("БАЦРФ261",),
        created_at=1,
    )
    rows = await seeded.homework_status(homework.id)
    assert all(row.status_label == "не сдано (0)" for row in rows)
    assert len(rows) == 28
