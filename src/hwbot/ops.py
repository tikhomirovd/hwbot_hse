from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from hwbot.course import Assessment as CourseAssessment
from hwbot.course import Course
from hwbot.course import Lesson as CourseLesson
from hwbot.db import Database
from hwbot.grading import GradeReport, StudentState, build_report
from hwbot.matching import match_students
from hwbot.models import Assessment, Lesson, Student
from hwbot.roster import load_roster


@dataclass(frozen=True, slots=True)
class SeedReport:
    created_lessons: int
    updated_lessons: int
    unchanged_lessons: int
    created_assessments: int
    updated_assessments: int
    unchanged_assessments: int
    changes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MatchFailure:
    query: str
    reason: str


@dataclass(frozen=True, slots=True)
class WriteReport:
    created: int
    updated: int
    failed: tuple[MatchFailure, ...]
    warnings: tuple[str, ...]


def _lesson_same(existing: Lesson, lesson: CourseLesson) -> bool:
    return (
        existing.kind == lesson.kind
        and existing.seminar_group == lesson.seminar_group
        and existing.topic == lesson.topic
        and existing.title == lesson.title
        and existing.starts_ts == lesson.starts_ts
        and existing.ends_ts == lesson.ends_ts
        and existing.room == lesson.room
        and existing.module == lesson.module
    )


def _assessment_same(existing: Assessment, assessment: CourseAssessment) -> bool:
    return (
        existing.label == assessment.label
        and existing.title == assessment.title
        and existing.body == assessment.summary
        and existing.component == assessment.component
        and existing.weight_final == assessment.weight_final
        and existing.submit_via_bot == assessment.submit_via_bot
        and existing.issued_at == assessment.issued_at
        and existing.deadline_ts == assessment.deadline_ts
        and existing.accept_until_ts == assessment.accept_until_ts
        and existing.graded_on_ts == assessment.graded_on_ts
        and existing.late_rule == assessment.late_rule
        and existing.blocking == assessment.blocking
    )


async def preview_seed(db: Database, course: Course) -> SeedReport:
    created_l = updated_l = unchanged_l = 0
    created_a = updated_a = unchanged_a = 0
    details: list[str] = []
    for lesson in course.lessons:
        existing = await db.get_lesson_by_code(lesson.code)
        if existing is None:
            created_l += 1
            details.append(f"+ занятие {lesson.code}")
        elif _lesson_same(existing, lesson):
            unchanged_l += 1
        else:
            updated_l += 1
            details.append(f"~ занятие {lesson.code}")
    for assessment in course.assessments:
        found = await db.get_assessment_by_code(assessment.code)
        if found is None:
            created_a += 1
            details.append(f"+ элемент {assessment.code}")
        elif _assessment_same(found, assessment):
            unchanged_a += 1
        else:
            updated_a += 1
            details.append(f"~ элемент {assessment.code}")
    return SeedReport(
        created_lessons=created_l,
        updated_lessons=updated_l,
        unchanged_lessons=unchanged_l,
        created_assessments=created_a,
        updated_assessments=updated_a,
        unchanged_assessments=unchanged_a,
        changes=tuple(details),
    )


async def seed_course(db: Database, course: Course) -> SeedReport:
    created_l = updated_l = unchanged_l = 0
    created_a = updated_a = unchanged_a = 0
    for lesson in course.lessons:
        result = await db.upsert_lesson(lesson)
        if result == "created":
            created_l += 1
        elif result == "updated":
            updated_l += 1
        else:
            unchanged_l += 1
    for assessment in course.assessments:
        result = await db.upsert_assessment(assessment)
        if result == "created":
            created_a += 1
        elif result == "updated":
            updated_a += 1
        else:
            unchanged_a += 1
    return SeedReport(
        created_lessons=created_l,
        updated_lessons=updated_l,
        unchanged_lessons=unchanged_l,
        created_assessments=created_a,
        updated_assessments=updated_a,
        unchanged_assessments=unchanged_a,
    )


def split_names(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def resolve_student(query: str, students: Sequence[Student]) -> Student | MatchFailure:
    result = match_students(query, students)
    unique = result.unique
    if unique is not None:
        return unique
    if result.students:
        return MatchFailure(query, "несколько совпадений")
    return MatchFailure(query, "не опознан")


def resolve_all(
    queries: Sequence[str], students: Sequence[Student]
) -> tuple[list[Student], list[MatchFailure]]:
    found: list[Student] = []
    failed: list[MatchFailure] = []
    for query in queries:
        item = resolve_student(query, students)
        if isinstance(item, MatchFailure):
            failed.append(item)
        else:
            found.append(item)
    return found, failed


def read_named_csv(path: Path) -> tuple[str, list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("Пустой CSV: нет заголовков")
        headers = [name.strip() for name in reader.fieldnames if name]
        if not headers:
            raise ValueError("Пустой CSV: нет заголовков")
        key = headers[0]
        if key not in {"full_name", "email"}:
            raise ValueError("Первая колонка должна быть full_name или email")
        rows: list[dict[str, str]] = []
        for raw in reader:
            row = {name.strip(): (value or "").strip() for name, value in raw.items() if name}
            if not row.get(key):
                continue
            rows.append(row)
        return key, rows


async def apply_attendance(
    db: Database,
    lesson: Lesson,
    assignments: Sequence[tuple[Student, str]],
    *,
    dry_run: bool,
) -> WriteReport:
    created = updated = 0
    warnings: list[str] = []
    for student, status in assignments:
        existing = await db.get_attendance(student.id, lesson.id)
        if existing is None:
            created += 1
        elif existing.status != status:
            updated += 1
        if dry_run:
            continue
        await db.mark_attendance(student.id, lesson.id, status)
        if lesson.kind == "seminar" and lesson.seminar_group:
            if student.seminar_group is None:
                await db.set_seminar_group(student.id, lesson.seminar_group)
                warnings.append(
                    f"проставил семинарскую группу {lesson.seminar_group} студенту id={student.id}"
                )
            elif student.seminar_group != lesson.seminar_group:
                warnings.append(
                    f"студент id={student.id} отмечен на семинаре группы "
                    f"{lesson.seminar_group}, но у него группа {student.seminar_group}"
                )
    return WriteReport(created, updated, (), tuple(warnings))


async def apply_grades(
    db: Database,
    assessment: Assessment,
    assignments: Sequence[tuple[Student, float, str]],
    *,
    dry_run: bool,
) -> WriteReport:
    created = updated = 0
    for student, score, comment in assignments:
        existing = await db.get_grade(student.id, assessment.id)
        if existing is None:
            created += 1
        elif existing.score != score or existing.comment != comment:
            updated += 1
        if dry_run:
            continue
        await db.set_grade(student.id, assessment.id, score, comment)
    return WriteReport(created, updated, (), ())


async def build_student_state(db: Database, student: Student, course: Course) -> StudentState:
    lessons = await db.list_lessons()
    by_id = {lesson.id: lesson for lesson in lessons}
    by_code = {lesson.code: lesson for lesson in lessons}
    _ = by_code
    marks = await db.list_attendance_for_student(student.id)
    attendance = {
        by_id[mark.lesson_id].code: mark.status
        for mark in marks
        if mark.lesson_id in by_id
    }
    held = await db.held_lesson_ids()
    held_codes = frozenset(by_id[lesson_id].code for lesson_id in held if lesson_id in by_id)
    assessments = await db.list_assessments()
    submissions: dict[str, int] = {}
    for assessment in assessments:
        latest = await db.latest_submission(student.id, assessment.id)
        if latest is not None:
            submissions[assessment.code] = latest.submitted_at
    grades: dict[str, float] = {}
    for grade in await db.list_grades_for_student(student.id):
        matched_assessment = next(
            (item for item in assessments if item.id == grade.assessment_id),
            None,
        )
        if matched_assessment is not None:
            grades[matched_assessment.code] = grade.score
    _ = course
    return StudentState(
        seminar_group=student.seminar_group,
        attendance=attendance,
        held_lesson_codes=held_codes,
        submissions=submissions,
        grades=grades,
    )


async def reports_for_students(
    db: Database, course: Course, now: int
) -> list[tuple[Student, GradeReport]]:
    students = await db.list_students()
    result: list[tuple[Student, GradeReport]] = []
    for student in students:
        state = await build_student_state(db, student, course)
        result.append((student, build_report(state, course, now)))
    return result


def apply_roster_seminar_groups(
    students: Sequence[Student], roster_path: Path
) -> list[tuple[Student, str]]:
    rows = load_roster(roster_path)
    by_name = {row.full_name: row.seminar_group for row in rows if row.seminar_group}
    updates: list[tuple[Student, str]] = []
    for student in students:
        group = by_name.get(student.full_name)
        if group and student.seminar_group != group:
            updates.append((student, group))
    return updates
