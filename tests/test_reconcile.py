from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator, Iterable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiosqlite
import pytest

from hwbot.cli import format_seed_plan, run_seed_course, stale_block_message
from hwbot.course import DEFAULT_COURSE_PATH, Course
from hwbot.course import Lesson as CourseLesson
from hwbot.course import load_course
from hwbot.db import Database
from hwbot.errors import StaleCourseStateError
from hwbot.models import Assessment
from hwbot.ops import apply_course_plan, plan_seed, seed_course
from hwbot.reconcile import (
    CoursePlan,
    CourseSnapshot,
    EntityKind,
    EntityPlan,
    FieldChange,
    ReconcileState,
    reconcile_course,
)

DAY = 86400


@pytest.fixture
def course() -> Course:
    return load_course(DEFAULT_COURSE_PATH)


@pytest.fixture
async def db_file(tmp_path: Path) -> AsyncIterator[tuple[Database, Path]]:
    path = tmp_path / "reconcile.db"
    database = Database(path)
    await database.connect()
    yield database, path
    await database.close()


def entry_for(plan_entries: Iterable[Any], code: str) -> Any:
    return next(item for item in plan_entries if item.code == code)


def with_lesson_title(course: Course, code: str, title: str) -> Course:
    return replace(
        course,
        lessons=tuple(
            replace(lesson, title=title) if lesson.code == code else lesson
            for lesson in course.lessons
        ),
    )


def with_deadline(course: Course, code: str, deadline_ts: int) -> Course:
    return replace(
        course,
        assessments=tuple(
            replace(item, deadline_ts=deadline_ts) if item.code == code else item
            for item in course.assessments
        ),
    )


def with_lesson_code(course: Course, old_code: str, new_code: str) -> Course:
    return replace(
        course,
        lessons=tuple(
            replace(lesson, code=new_code) if lesson.code == old_code else lesson
            for lesson in course.lessons
        ),
    )


def extra_lesson(code: str) -> CourseLesson:
    return CourseLesson(
        code=code,
        kind="lecture",
        seminar_group=None,
        topic=1,
        title="Занятие из прошлого семестра",
        starts_ts=1_600_000_000,
        ends_ts=1_600_005_400,
        room=None,
        module=1,
        note=None,
    )


def db_assessment(code: str, *, active: bool) -> Assessment:
    return Assessment(
        id=900,
        code=code,
        label=code,
        title=code,
        body="",
        component="homework",
        weight_final=0.0,
        submit_via_bot=True,
        issued_at=None,
        deadline_ts=None,
        accept_until_ts=None,
        graded_on_ts=None,
        late_rule="homework",
        blocking=False,
        active=active,
    )


async def test_empty_database_plans_creates(db: Database, course: Course) -> None:
    plan = await plan_seed(db, course)
    assert plan.created_lessons == 36
    assert plan.created_assessments == 11
    assert plan.updated_lessons == 0
    assert plan.updated_assessments == 0
    assert not plan.has_stale


async def test_identical_state_is_unchanged(db: Database, course: Course) -> None:
    await seed_course(db, course)
    plan = await plan_seed(db, course)
    assert plan.unchanged_lessons == 36
    assert plan.unchanged_assessments == 11
    assert plan.writes == ()
    assert not plan.has_stale


async def test_changed_lesson_field_reports_exact_diff(
    db: Database, course: Course
) -> None:
    await seed_course(db, course)
    plan = await plan_seed(db, with_lesson_title(course, "L01", "Новое название"))
    assert plan.updated_lessons == 1
    entry = entry_for(plan.select(EntityKind.LESSON, ReconcileState.UPDATE), "L01")
    assert len(entry.changes) == 1
    change = entry.changes[0]
    assert change.field == "title"
    assert change.after == "Новое название"
    assert change.before != change.after


async def test_changed_deadline_reports_exact_diff(
    db: Database, course: Course
) -> None:
    await seed_course(db, course)
    original = course.assessment_by_code("hw1")
    assert original.deadline_ts is not None
    moved = original.deadline_ts + DAY
    plan = await plan_seed(db, with_deadline(course, "hw1", moved))
    assert plan.updated_assessments == 1
    entry = entry_for(
        plan.select(EntityKind.ASSESSMENT, ReconcileState.UPDATE), "hw1"
    )
    assert [change.field for change in entry.changes] == ["deadline_ts"]
    assert entry.changes[0].before == original.deadline_ts
    assert entry.changes[0].after == moved


async def test_extra_active_assessment_is_stale(db: Database, course: Course) -> None:
    await seed_course(db, course)
    await db.create_homework(
        "Разовое ДЗ", "текст", deadline_ts=2_000_000_000, group_codes=()
    )
    plan = await plan_seed(db, course)
    assert plan.stale_assessments == 1
    assert plan.has_stale
    assert plan.stale[0].kind is EntityKind.ASSESSMENT


async def test_extra_lesson_is_stale(db: Database, course: Course) -> None:
    await seed_course(db, course)
    await db.upsert_lesson(extra_lesson("OLD01"))
    plan = await plan_seed(db, course)
    assert plan.stale_lessons == 1
    assert plan.stale[0].code == "OLD01"
    assert plan.has_stale


def test_inactive_assessment_absent_from_toml_does_not_block(course: Course) -> None:
    snapshot = CourseSnapshot(
        lessons={},
        assessments={"old_hw": db_assessment("old_hw", active=False)},
    )
    plan = reconcile_course(course, snapshot)
    assert not plan.has_stale
    assert plan.stale_assessments == 0
    assert all(entry.code != "old_hw" for entry in plan.entries)


def test_active_assessment_absent_from_toml_blocks(course: Course) -> None:
    snapshot = CourseSnapshot(
        lessons={},
        assessments={"old_hw": db_assessment("old_hw", active=True)},
    )
    plan = reconcile_course(course, snapshot)
    assert plan.stale_assessments == 1
    assert plan.has_stale


async def test_inactive_assessment_in_toml_is_updated_and_reactivated(
    db_file: tuple[Database, Path], course: Course
) -> None:
    db, path = db_file
    await seed_course(db, course)
    with sqlite3.connect(path) as raw:
        raw.execute("UPDATE assessments SET active = 0 WHERE code = ?", ("hw1",))
        raw.commit()

    plan = await plan_seed(db, course)
    entry = entry_for(
        plan.select(EntityKind.ASSESSMENT, ReconcileState.UPDATE), "hw1"
    )
    assert [change.field for change in entry.changes] == ["active"]
    assert entry.changes[0].before is False
    assert entry.changes[0].after is True

    await apply_course_plan(db, course, plan)
    restored = await db.get_assessment_by_code("hw1")
    assert restored is not None
    assert restored.active is True


async def test_code_rename_is_create_plus_stale(db: Database, course: Course) -> None:
    await seed_course(db, course)
    plan = await plan_seed(db, with_lesson_code(course, "L01", "L01B"))
    created = plan.select(EntityKind.LESSON, ReconcileState.CREATE)
    stale = plan.select(EntityKind.LESSON, ReconcileState.STALE)
    assert [item.code for item in created] == ["L01B"]
    assert [item.code for item in stale] == ["L01"]
    assert plan.updated_lessons == 0


async def test_seed_course_raises_on_stale_and_writes_nothing(
    db: Database, course: Course
) -> None:
    await seed_course(db, course)
    await db.upsert_lesson(extra_lesson("OLD01"))
    before = await db.get_lesson_by_code("L01")
    assert before is not None
    lessons_before = await db.count_lessons()
    assessments_before = await db.count_assessments()

    drifted = with_lesson_title(course, "L01", "Название, которое не должно записаться")
    pending = await plan_seed(db, drifted)
    assert pending.has_stale
    assert pending.updated_lessons == 1

    with pytest.raises(StaleCourseStateError):
        await seed_course(db, drifted)

    after = await db.get_lesson_by_code("L01")
    assert after is not None
    assert after.title == before.title
    assert await db.count_lessons() == lessons_before
    assert await db.count_assessments() == assessments_before


async def test_dry_run_writes_nothing(db: Database, course: Course) -> None:
    exit_code = await run_seed_course(db, course, dry_run=True)
    assert exit_code == 0
    assert await db.count_lessons() == 0
    assert await db.count_assessments() == 0


async def test_seed_is_idempotent(db: Database, course: Course) -> None:
    first = await run_seed_course(db, course, dry_run=False)
    assert first == 0
    assert await db.count_lessons() == 36
    assert await db.count_assessments() == 11
    second = await plan_seed(db, course)
    assert second.writes == ()
    assert second.unchanged_lessons == 36
    assert second.unchanged_assessments == 11


async def test_failed_apply_rolls_back_every_write(
    db: Database, course: Course, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = await plan_seed(db, course)
    original = aiosqlite.Connection.execute
    seen = 0

    async def failing(
        self: aiosqlite.Connection,
        sql: str,
        parameters: Iterable[Any] | None = None,
    ) -> aiosqlite.Cursor:
        nonlocal seen
        if sql.lstrip().startswith("INSERT INTO lessons"):
            seen += 1
            if seen > 5:
                raise RuntimeError("боль в середине применения")
        return await original(self, sql, parameters)

    monkeypatch.setattr(aiosqlite.Connection, "execute", failing)
    with pytest.raises(RuntimeError):
        await apply_course_plan(db, course, plan)
    monkeypatch.undo()

    assert seen == 6
    assert await db.count_lessons() == 0
    assert await db.count_assessments() == 0


async def test_reconciliation_reads_do_not_grow_with_entities(
    db: Database, course: Course, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    original = aiosqlite.Connection.execute

    async def counting(
        self: aiosqlite.Connection,
        sql: str,
        parameters: Iterable[Any] | None = None,
    ) -> aiosqlite.Cursor:
        calls.append(sql)
        return await original(self, sql, parameters)

    monkeypatch.setattr(aiosqlite.Connection, "execute", counting)
    await plan_seed(db, course)
    empty_reads = len(calls)
    monkeypatch.undo()

    await seed_course(db, course)
    calls.clear()
    monkeypatch.setattr(aiosqlite.Connection, "execute", counting)
    await plan_seed(db, course)
    assert len(calls) == empty_reads == 2


async def test_cli_returns_non_zero_and_reports_drift(
    db: Database, course: Course, capsys: pytest.CaptureFixture[str]
) -> None:
    await seed_course(db, course)
    await db.upsert_lesson(extra_lesson("OLD01"))
    await db.create_homework(
        "Разовое ДЗ", "текст", deadline_ts=2_000_000_000, group_codes=()
    )

    exit_code = await run_seed_course(db, course, dry_run=False)
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "! stale занятие OLD01" in captured.out
    assert "stale 1" in captured.out
    assert "seed-course не применён" in captured.err
    assert "занятия и элементы" in captured.err


async def test_dry_run_reports_stale_without_failing(
    db: Database, course: Course, capsys: pytest.CaptureFixture[str]
) -> None:
    await seed_course(db, course)
    await db.upsert_lesson(extra_lesson("OLD01"))
    exit_code = await run_seed_course(db, course, dry_run=True)
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "dry-run, занятия:" in captured.out
    assert "! stale занятие OLD01" in captured.out


async def test_plan_rendering_shows_human_values(
    db: Database, course: Course
) -> None:
    await seed_course(db, course)
    original = course.assessment_by_code("hw1")
    assert original.deadline_ts is not None
    plan = await plan_seed(db, with_deadline(course, "hw1", original.deadline_ts + DAY))
    text = format_seed_plan(plan, dry_run=True, timezone=course.timezone)
    assert "dry-run, элементы: создано 0, обновлено 1" in text
    assert "~ элемент hw1" in text
    assert "  deadline: 19.09.2026 23:59 -> 20.09.2026 23:59" in text
    assert "->" in text
    assert str(original.deadline_ts) not in text


def test_stale_block_message_names_only_affected_kinds(course: Course) -> None:
    snapshot = CourseSnapshot(
        lessons={},
        assessments={"old_hw": db_assessment("old_hw", active=True)},
    )
    plan = reconcile_course(course, snapshot)
    message = stale_block_message(plan)
    assert "элементы" in message
    assert "занятия" not in message


async def test_commit_failure_rolls_back_and_propagates(
    db: Database, course: Course, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = await plan_seed(db, course)
    original_execute = aiosqlite.Connection.execute
    original_rollback = aiosqlite.Connection.rollback
    writes = 0
    rollbacks = 0

    async def counting_execute(
        self: aiosqlite.Connection,
        sql: str,
        parameters: Iterable[Any] | None = None,
    ) -> aiosqlite.Cursor:
        nonlocal writes
        if sql.lstrip().startswith("INSERT INTO"):
            writes += 1
        return await original_execute(self, sql, parameters)

    async def failing_commit(self: aiosqlite.Connection) -> None:
        _ = self
        raise sqlite3.OperationalError("disk I/O error")

    async def counting_rollback(self: aiosqlite.Connection) -> None:
        nonlocal rollbacks
        rollbacks += 1
        await original_rollback(self)

    monkeypatch.setattr(aiosqlite.Connection, "execute", counting_execute)
    monkeypatch.setattr(aiosqlite.Connection, "commit", failing_commit)
    monkeypatch.setattr(aiosqlite.Connection, "rollback", counting_rollback)
    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        await apply_course_plan(db, course, plan)
    monkeypatch.undo()

    assert writes == len(plan.writes) == 47
    assert rollbacks == 1
    assert await db.count_lessons() == 0
    assert await db.count_assessments() == 0

    await seed_course(db, course)
    assert await db.count_lessons() == 36
    assert await db.count_assessments() == 11


def deadline_plan(before: int, after: int) -> CoursePlan:
    return CoursePlan(
        (
            EntityPlan(
                kind=EntityKind.ASSESSMENT,
                code="hw1",
                state=ReconcileState.UPDATE,
                changes=(FieldChange("deadline_ts", before, after),),
            ),
        )
    )


def deadline_line(text: str) -> str:
    return next(line for line in text.splitlines() if "deadline:" in line)


def test_change_within_one_minute_renders_seconds() -> None:
    moscow = ZoneInfo("Europe/Moscow")
    base = int(datetime(2026, 9, 19, 19, 30, tzinfo=moscow).timestamp())
    text = format_seed_plan(
        deadline_plan(base, base + 30), dry_run=True, timezone="Europe/Moscow"
    )
    assert deadline_line(text) == (
        "  deadline: 19.09.2026 19:30:00 -> 19.09.2026 19:30:30"
    )


def test_timestamps_render_in_the_given_course_timezone() -> None:
    moment = int(datetime(2026, 9, 19, 20, 59, tzinfo=ZoneInfo("UTC")).timestamp())
    plan = deadline_plan(moment, moment + DAY)
    moscow = format_seed_plan(plan, dry_run=True, timezone="Europe/Moscow")
    tokyo = format_seed_plan(plan, dry_run=True, timezone="Asia/Tokyo")
    assert deadline_line(moscow) == "  deadline: 19.09.2026 23:59 -> 20.09.2026 23:59"
    assert deadline_line(tokyo) == "  deadline: 20.09.2026 05:59 -> 21.09.2026 05:59"


def test_repeated_wall_clock_hour_still_renders_as_a_change() -> None:
    berlin = ZoneInfo("Europe/Berlin")
    first = int(datetime(2026, 10, 25, 2, 30, tzinfo=berlin, fold=0).timestamp())
    second = int(datetime(2026, 10, 25, 2, 30, tzinfo=berlin, fold=1).timestamp())
    assert second - first == 3600
    text = format_seed_plan(
        deadline_plan(first, second), dry_run=True, timezone="Europe/Berlin"
    )
    before, after = deadline_line(text).split(": ", 1)[1].split(" -> ")
    assert before != after
    assert before.endswith("+0200")
    assert after.endswith("+0100")
