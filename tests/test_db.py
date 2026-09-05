from __future__ import annotations

import pytest

from hwbot.course import DEFAULT_COURSE_PATH, load_course
from hwbot.db import Database
from hwbot.errors import DeadlineClosedError, NotIssuedError
from hwbot.ops import seed_course
from hwbot.roster import load_roster
from hwbot.config import PROJECT_ROOT


@pytest.fixture
async def seeded(db: Database) -> Database:
    await db.seed_roster(load_roster(PROJECT_ROOT / "data" / "roster.csv"))
    return db


async def test_seed_count(seeded: Database) -> None:
    assert await seeded.student_count() == 57
    students = await seeded.list_students()
    assert {item.seminar_group for item in students if item.group_code == "БАЦРФ261"} == {
        "261"
    }
    assert {item.seminar_group for item in students if item.group_code == "БАЦРФ262"} == {
        "262"
    }


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


async def test_accept_after_deadline_before_accept_until(seeded: Database) -> None:
    students = await seeded.list_students()
    student = next(item for item in students if item.group_code == "БАЦРФ261")
    homework = await seeded.create_homework(
        "ДЗ late",
        "text",
        deadline_ts=1_000,
        group_codes=("БАЦРФ261",),
        created_at=1,
    )
    submission = await seeded.add_submission(
        student.id, homework.id, "https://github.com/x", submitted_at=1_001
    )
    assert submission.payload.startswith("https://github.com")


async def test_reject_after_accept_until(seeded: Database) -> None:
    students = await seeded.list_students()
    student = next(item for item in students if item.group_code == "БАЦРФ261")
    homework = await seeded.create_homework(
        "ДЗ closed",
        "text",
        deadline_ts=1_000,
        group_codes=("БАЦРФ261",),
        created_at=1,
    )
    assert homework.accept_until_ts is not None
    with pytest.raises(DeadlineClosedError):
        await seeded.add_submission(
            student.id,
            homework.id,
            "https://github.com/x",
            submitted_at=homework.accept_until_ts + 1,
        )


async def test_seed_course_idempotent(db: Database) -> None:
    course = load_course(DEFAULT_COURSE_PATH)
    first = await seed_course(db, course)
    assert first.created_lessons == 36
    assert first.created_assessments == 11
    assert first.updated_lessons == 0
    second = await seed_course(db, course)
    assert second.created_lessons == 0
    assert second.updated_lessons == 0
    assert second.created_assessments == 0
    assert second.updated_assessments == 0
    assert await db.count_lessons() == 36
    assert await db.count_assessments() == 11


async def test_grade_set_updates(seeded: Database) -> None:
    course = load_course(DEFAULT_COURSE_PATH)
    await seed_course(seeded, course)
    students = await seeded.list_students()
    student = next(item for item in students if "Абрамова" in item.full_name)
    assessment = await seeded.get_assessment_by_code("hw1")
    assert assessment is not None
    assert await seeded.set_grade(student.id, assessment.id, 8.5) == "created"
    assert await seeded.set_grade(student.id, assessment.id, 9.0, "ок") == "updated"
    grade = await seeded.get_grade(student.id, assessment.id)
    assert grade is not None
    assert grade.score == 9.0
    assert grade.comment == "ок"


async def test_reject_before_issued_at(seeded: Database) -> None:
    students = await seeded.list_students()
    student = next(item for item in students if item.group_code == "БАЦРФ261")
    homework = await seeded.create_homework(
        "ДЗ future",
        "text",
        deadline_ts=2_000_000,
        group_codes=("БАЦРФ261",),
        created_at=1_500_000,
    )
    with pytest.raises(NotIssuedError):
        await seeded.add_submission(
            student.id,
            homework.id,
            "https://github.com/x",
            submitted_at=1_000_000,
        )


async def test_bind_sets_registered_at(seeded: Database) -> None:
    students = await seeded.list_students()
    student = next(item for item in students if "Абрамова" in item.full_name)
    bound = await seeded.bind_telegram(student.id, 222, "abra")
    assert bound.registered_at is not None
    await seeded.unbind_telegram(student.id)
    unbound = await seeded.get_student(student.id)
    assert unbound is not None
    assert unbound.telegram_id is None


async def test_status_open_for_missing(seeded: Database) -> None:
    homework = await seeded.create_homework(
        "ДЗ status",
        "text",
        deadline_ts=2_000_000_000,
        group_codes=("БАЦРФ261",),
        created_at=1,
    )
    rows = await seeded.homework_status(homework.id)
    assert all(row.status_label == "не сдано" for row in rows)
    assert len(rows) == 57
