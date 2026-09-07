from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

import aiosqlite
import pytest
from aiogram.types import Message, User

from hwbot.cli import build_parser, format_overview_text
from hwbot.config import Settings
from hwbot.course import Lesson as CourseLesson
from hwbot.db import Database
from hwbot.formatting import format_course_overview
from hwbot.handlers.admin import cmd_overview
from hwbot.models import Lesson, Student
from hwbot.overview import (
    ActionSummary,
    AssessmentOverview,
    AttendanceGapKind,
    AttendanceOverview,
    CourseOverview,
    LessonAttendanceGap,
    RegistrationOverview,
    build_action_summary,
    build_attendance_overview,
    build_course_overview,
)
from hwbot.roster import load_roster
from hwbot.telegramutil import MESSAGE_LIMIT, answer_long

NOW = 2_000_000_000
DAY = 86400
ADMIN_ID = 77


@pytest.fixture
async def seeded(db: Database, roster_path: Path) -> Database:
    await db.seed_roster(load_roster(roster_path))
    return db


def course_lesson(
    code: str,
    kind: str,
    seminar_group: str | None,
    *,
    ends_ts: int,
) -> CourseLesson:
    return CourseLesson(
        code=code,
        kind=kind,
        seminar_group=seminar_group,
        topic=1,
        title=f"Занятие {code}",
        starts_ts=ends_ts - 5400,
        ends_ts=ends_ts,
        room=None,
        module=1,
        note=None,
    )


async def students_of(db: Database, group_code: str) -> list[Student]:
    return [item for item in await db.list_students() if item.group_code == group_code]


async def open_homework(db: Database, title: str = "ДЗ 1") -> int:
    homework = await db.create_homework(
        title,
        "Ссылка на репозиторий",
        deadline_ts=NOW + 3 * DAY,
        group_codes=("БАЦРФ261", "БАЦРФ262"),
        created_at=NOW - 5 * DAY,
    )
    return homework.id


async def closed_homework(db: Database, title: str = "ДЗ прошлой недели") -> int:
    homework = await db.create_homework(
        title,
        "Ссылка на репозиторий",
        deadline_ts=NOW - 10 * DAY,
        group_codes=("БАЦРФ261", "БАЦРФ262"),
        created_at=NOW - 17 * DAY,
    )
    assert homework.accept_until_ts is not None
    assert homework.accept_until_ts < NOW
    return homework.id


async def test_no_registered_students(seeded: Database) -> None:
    overview = await build_course_overview(seeded, NOW)
    assert overview.registration.total == 4
    assert overview.registration.registered == 0
    assert overview.registration.unregistered == 4
    assert overview.actions.unregistered == 4
    assert overview.actions.has_actions


async def test_partially_registered_cohort(seeded: Database) -> None:
    students = await seeded.list_students()
    await seeded.bind_telegram(students[0].id, 101, "one")
    await seeded.bind_telegram(students[1].id, 102, "two")
    overview = await build_course_overview(seeded, NOW)
    assert overview.registration.registered == 2
    assert overview.registration.unregistered == 2


async def test_assessment_with_submitted_and_missing(seeded: Database) -> None:
    homework_id = await open_homework(seeded)
    students = await seeded.list_students()
    await seeded.add_submission(students[0].id, homework_id, "https://git/one", NOW)
    await seeded.add_submission(students[1].id, homework_id, "https://git/two", NOW)
    overview = await build_course_overview(seeded, NOW)
    assert len(overview.assessments) == 1
    item = overview.assessments[0]
    assert item.submitted == 2
    assert item.missing == 2
    assert item.open_for_submissions is True


async def test_not_submitted_yet_is_not_a_teacher_action(seeded: Database) -> None:
    await open_homework(seeded)
    for index, student in enumerate(await seeded.list_students()):
        await seeded.bind_telegram(student.id, 200 + index, f"user{index}")
    overview = await build_course_overview(seeded, NOW)
    item = overview.assessments[0]
    assert item.submitted == 0
    assert item.missing == 4
    assert overview.actions.awaiting_review == 0
    assert not overview.actions.has_actions


async def test_submission_without_grade_awaits_review(seeded: Database) -> None:
    homework_id = await open_homework(seeded)
    students = await seeded.list_students()
    await seeded.add_submission(students[0].id, homework_id, "https://git/one", NOW)
    overview = await build_course_overview(seeded, NOW)
    item = overview.assessments[0]
    assert item.awaiting_review == 1
    assert item.reviewed == 0
    assert overview.actions.awaiting_review == 1


async def test_grade_after_submission_marks_it_reviewed(seeded: Database) -> None:
    homework_id = await open_homework(seeded)
    students = await seeded.list_students()
    await seeded.add_submission(
        students[0].id, homework_id, "https://git/one", NOW - DAY
    )
    await seeded.set_grade(students[0].id, homework_id, 8.0, graded_at=NOW)
    overview = await build_course_overview(seeded, NOW)
    item = overview.assessments[0]
    assert item.reviewed == 1
    assert item.awaiting_review == 0
    assert overview.actions.awaiting_review == 0


async def test_resubmission_after_grade_awaits_review_again(seeded: Database) -> None:
    homework_id = await open_homework(seeded)
    students = await seeded.list_students()
    await seeded.add_submission(
        students[0].id, homework_id, "https://git/first", NOW - 2 * DAY
    )
    await seeded.set_grade(students[0].id, homework_id, 6.0, graded_at=NOW - DAY)
    await seeded.add_submission(
        students[0].id, homework_id, "https://git/second", NOW
    )
    overview = await build_course_overview(seeded, NOW)
    item = overview.assessments[0]
    assert item.submitted == 1
    assert item.reviewed == 0
    assert item.awaiting_review == 1


async def test_manual_grade_without_submission_is_not_a_reviewed_submission(
    seeded: Database,
) -> None:
    homework_id = await open_homework(seeded)
    students = await seeded.list_students()
    await seeded.set_grade(students[0].id, homework_id, 0.0, graded_at=NOW)
    overview = await build_course_overview(seeded, NOW)
    item = overview.assessments[0]
    assert item.submitted == 0
    assert item.reviewed == 0
    assert item.awaiting_review == 0
    assert item.missing == 4


async def test_reviewed_plus_awaiting_equals_submitted(seeded: Database) -> None:
    homework_id = await open_homework(seeded)
    students = await seeded.list_students()
    await seeded.add_submission(
        students[0].id, homework_id, "https://git/graded", NOW - 2 * DAY
    )
    await seeded.set_grade(students[0].id, homework_id, 9.0, graded_at=NOW - DAY)
    await seeded.add_submission(students[1].id, homework_id, "https://git/fresh", NOW)
    await seeded.add_submission(
        students[2].id, homework_id, "https://git/first", NOW - 2 * DAY
    )
    await seeded.set_grade(students[2].id, homework_id, 5.0, graded_at=NOW - DAY)
    await seeded.add_submission(students[2].id, homework_id, "https://git/again", NOW)
    overview = await build_course_overview(seeded, NOW)
    item = overview.assessments[0]
    assert item.submitted == 3
    assert item.reviewed == 1
    assert item.awaiting_review == 2
    assert item.reviewed + item.awaiting_review == item.submitted
    assert item.missing == 1


async def test_closed_assessment_with_unreviewed_submission_stays_visible(
    seeded: Database,
) -> None:
    homework_id = await closed_homework(seeded)
    students = await seeded.list_students()
    await seeded.add_submission(
        students[0].id, homework_id, "https://git/one", NOW - 12 * DAY
    )
    overview = await build_course_overview(seeded, NOW)
    assert len(overview.assessments) == 1
    item = overview.assessments[0]
    assert item.open_for_submissions is False
    assert item.awaiting_review == 1
    assert overview.actions.awaiting_review == 1
    assert overview.actions.has_actions


async def test_closed_and_fully_reviewed_assessment_disappears(
    seeded: Database,
) -> None:
    homework_id = await closed_homework(seeded)
    students = await seeded.list_students()
    await seeded.add_submission(
        students[0].id, homework_id, "https://git/one", NOW - 12 * DAY
    )
    await seeded.set_grade(students[0].id, homework_id, 7.0, graded_at=NOW - 2 * DAY)
    overview = await build_course_overview(seeded, NOW)
    assert overview.assessments == ()
    assert overview.actions.awaiting_review == 0


async def test_closed_assessment_without_submissions_disappears(
    seeded: Database,
) -> None:
    await closed_homework(seeded)
    overview = await build_course_overview(seeded, NOW)
    assert overview.assessments == ()


async def test_not_yet_issued_assessment_is_not_relevant(seeded: Database) -> None:
    await seeded.create_homework(
        "ДЗ следующего модуля",
        "текст",
        deadline_ts=NOW + 20 * DAY,
        group_codes=("БАЦРФ261",),
        created_at=NOW + 10 * DAY,
    )
    overview = await build_course_overview(seeded, NOW)
    assert overview.assessments == ()


async def test_ended_lesson_without_marks_is_not_entered(seeded: Database) -> None:
    await seeded.upsert_lesson(course_lesson("L01", "lecture", None, ends_ts=NOW - DAY))
    overview = await build_course_overview(seeded, NOW)
    assert overview.attendance.ended_lessons == 1
    assert len(overview.attendance.not_entered) == 1
    assert overview.attendance.incomplete == ()
    gap = overview.attendance.not_entered[0]
    assert gap.state is AttendanceGapKind.NOT_ENTERED
    assert (gap.expected, gap.marked) == (4, 0)
    assert overview.actions.lessons_without_attendance == 1
    assert overview.actions.lessons_with_partial_attendance == 0


async def test_partially_marked_ended_lesson_is_incomplete(seeded: Database) -> None:
    await seeded.upsert_lesson(course_lesson("L01", "lecture", None, ends_ts=NOW - DAY))
    lesson = await seeded.get_lesson_by_code("L01")
    assert lesson is not None
    students = await seeded.list_students()
    for student in students[:3]:
        await seeded.mark_attendance(student.id, lesson.id, "present", marked_at=NOW)
    overview = await build_course_overview(seeded, NOW)
    assert overview.attendance.not_entered == ()
    assert len(overview.attendance.incomplete) == 1
    gap = overview.attendance.incomplete[0]
    assert gap.state is AttendanceGapKind.INCOMPLETE
    assert (gap.expected, gap.marked, gap.unmarked) == (4, 3, 1)
    assert overview.actions.lessons_with_partial_attendance == 1

    await seeded.mark_attendance(students[3].id, lesson.id, "absent", marked_at=NOW)
    complete = await build_course_overview(seeded, NOW)
    assert complete.attendance.gaps == ()
    assert complete.attendance.fully_marked_lessons == 1


async def test_upcoming_lesson_is_not_a_gap(seeded: Database) -> None:
    await seeded.upsert_lesson(course_lesson("L02", "lecture", None, ends_ts=NOW + DAY))
    overview = await build_course_overview(seeded, NOW)
    assert overview.attendance.ended_lessons == 0
    assert overview.attendance.gaps == ()


async def test_seminar_attendance_respects_seminar_group(seeded: Database) -> None:
    await seeded.upsert_lesson(course_lesson("S01A", "seminar", "261", ends_ts=NOW - DAY))
    lesson = await seeded.get_lesson_by_code("S01A")
    assert lesson is not None
    for student in await students_of(seeded, "БАЦРФ261"):
        await seeded.mark_attendance(student.id, lesson.id, "present", marked_at=NOW)
    overview = await build_course_overview(seeded, NOW)
    assert overview.attendance.gaps == ()
    assert overview.attendance.fully_marked_lessons == 1


async def test_seminar_gap_counts_only_its_own_group(seeded: Database) -> None:
    await seeded.upsert_lesson(course_lesson("S01B", "seminar", "262", ends_ts=NOW - DAY))
    lesson = await seeded.get_lesson_by_code("S01B")
    assert lesson is not None
    group = await students_of(seeded, "БАЦРФ262")
    await seeded.mark_attendance(group[0].id, lesson.id, "present", marked_at=NOW)
    overview = await build_course_overview(seeded, NOW)
    assert len(overview.attendance.incomplete) == 1
    gap = overview.attendance.incomplete[0]
    assert gap.seminar_group == "262"
    assert (gap.expected, gap.marked) == (2, 1)


def test_unknown_seminar_group_is_reported_not_hidden() -> None:
    students = [
        Student(1, "А", "БАЦРФ261", "a@example.edu", None, None, seminar_group="261"),
        Student(2, "Б", "БАЦРФ262", "b@example.edu", None, None, seminar_group=None),
    ]
    seminar = Lesson(
        id=10,
        code="S01A",
        kind="seminar",
        seminar_group="261",
        topic=1,
        title="Семинар",
        starts_ts=NOW - DAY,
        ends_ts=NOW - DAY + 5400,
        room=None,
        module=1,
    )
    overview = build_attendance_overview([seminar], students, {10: {1}}, NOW)
    assert overview.gaps == ()
    assert overview.students_with_unknown_seminar_group == 1


async def test_overview_query_count_does_not_grow_with_data(
    seeded: Database, monkeypatch: pytest.MonkeyPatch
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

    homework_id = await open_homework(seeded)
    await seeded.upsert_lesson(course_lesson("L01", "lecture", None, ends_ts=NOW - DAY))
    monkeypatch.setattr(aiosqlite.Connection, "execute", counting)
    await build_course_overview(seeded, NOW)
    empty_queries = len(calls)

    monkeypatch.undo()
    students = await seeded.list_students()
    lesson = await seeded.get_lesson_by_code("L01")
    assert lesson is not None
    for student in students:
        await seeded.add_submission(student.id, homework_id, "https://git/x", NOW)
        await seeded.set_grade(student.id, homework_id, 7.0, graded_at=NOW)
        await seeded.mark_attendance(student.id, lesson.id, "present", marked_at=NOW)

    calls.clear()
    monkeypatch.setattr(aiosqlite.Connection, "execute", counting)
    await build_course_overview(seeded, NOW)
    assert len(calls) == empty_queries == 6


async def test_cli_and_telegram_agree_on_the_same_numbers(seeded: Database) -> None:
    homework_id = await open_homework(seeded)
    students = await seeded.list_students()
    await seeded.bind_telegram(students[0].id, 101, "one")
    await seeded.add_submission(students[0].id, homework_id, "https://git/one", NOW)
    await seeded.upsert_lesson(course_lesson("L01", "lecture", None, ends_ts=NOW - DAY))
    lesson = await seeded.get_lesson_by_code("L01")
    assert lesson is not None
    await seeded.mark_attendance(students[0].id, lesson.id, "present", marked_at=NOW)

    overview = await build_course_overview(seeded, NOW)
    terminal = format_overview_text(overview)
    telegram = format_course_overview(overview)
    for text in (terminal, telegram):
        assert "сдали 1" in text
        assert "пока не сдали 3" in text
        assert "проверено 0" in text
        assert "ждут проверки 1" in text
        assert "без отметки 3 из 4" in text


def test_overview_subcommand_is_registered() -> None:
    args = build_parser().parse_args(["overview"])
    assert args.command == "overview"


class FakeMessage:
    def __init__(self, user_id: int) -> None:
        self.from_user = User(id=user_id, is_bot=False, first_name="Кто-то")
        self.answers: list[str] = []

    async def answer(self, text: str, reply_markup: object = None) -> None:
        _ = reply_markup
        self.answers.append(text)


def admin_settings() -> Settings:
    return Settings(
        bot_token="test",
        admin_telegram_ids=frozenset({ADMIN_ID}),
        db_path=Path("bot.db"),
        roster_path=Path("roster.csv"),
        timezone="Europe/Moscow",
    )


async def test_overview_command_is_admin_only(seeded: Database) -> None:
    student = FakeMessage(ADMIN_ID + 1)
    await cmd_overview(cast(Message, student), seeded, admin_settings())
    assert student.answers == ["Это команда преподавателя."]

    admin = FakeMessage(ADMIN_ID)
    await cmd_overview(cast(Message, admin), seeded, admin_settings())
    assert len(admin.answers) == 1
    assert "Регистрация" in admin.answers[0]


def overview_with_gaps(count: int, title: str) -> CourseOverview:
    gaps = tuple(
        LessonAttendanceGap(
            code=f"L{index:02d}",
            title=title,
            seminar_group="261",
            expected=30,
            marked=0,
            state=AttendanceGapKind.NOT_ENTERED,
        )
        for index in range(count)
    )
    attendance = AttendanceOverview(
        ended_lessons=count,
        fully_marked_lessons=0,
        gaps=gaps,
        students_with_unknown_seminar_group=0,
    )
    registration = RegistrationOverview(total=30, registered=30)
    return CourseOverview(
        as_of_ts=NOW,
        registration=registration,
        assessments=(),
        attendance=attendance,
        actions=build_action_summary(registration, (), attendance),
    )


async def test_long_overview_is_sent_in_safe_chunks() -> None:
    overview = overview_with_gaps(36, "Занятие с очень длинным названием " * 4)
    text = format_course_overview(overview)
    assert len(text) > MESSAGE_LIMIT
    admin = FakeMessage(ADMIN_ID)
    await answer_long(cast(Message, admin), text)
    assert len(admin.answers) > 1
    assert all(len(part) <= MESSAGE_LIMIT for part in admin.answers)


def test_telegram_overview_escapes_dynamic_text() -> None:
    registration = RegistrationOverview(total=1, registered=1)
    attendance = AttendanceOverview(
        ended_lessons=1,
        fully_marked_lessons=0,
        gaps=(
            LessonAttendanceGap(
                code="<L01>",
                title="Тема <b>жирная</b>",
                seminar_group="2<6>1",
                expected=2,
                marked=1,
                state=AttendanceGapKind.INCOMPLETE,
            ),
        ),
        students_with_unknown_seminar_group=0,
    )
    overview = CourseOverview(
        as_of_ts=NOW,
        registration=registration,
        assessments=(
            AssessmentOverview(
                code="hw1",
                label="ДЗ <i>1</i>",
                deadline_ts=None,
                open_for_submissions=True,
                submitted=1,
                missing=0,
                reviewed=0,
                awaiting_review=1,
            ),
        ),
        attendance=attendance,
        actions=ActionSummary(
            unregistered=0,
            awaiting_review=1,
            lessons_without_attendance=0,
            lessons_with_partial_attendance=1,
            students_with_unknown_seminar_group=0,
        ),
    )
    text = format_course_overview(overview)
    for raw in ("<i>1</i>", "<b>жирная</b>", "<L01>", "2<6>1"):
        assert raw not in text
    for escaped in ("&lt;i&gt;1&lt;/i&gt;", "&lt;L01&gt;", "2&lt;6&gt;1"):
        assert escaped in text
