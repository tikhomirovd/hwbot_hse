from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from hwbot.availability import is_active_bot_work, is_accept_open, is_current, is_issued
from hwbot.db import Database
from hwbot.groups import attends_lesson
from hwbot.models import Assessment, Grade, Lesson, Student, Submission


class AttendanceGapKind(Enum):
    NOT_ENTERED = "not_entered"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True, slots=True)
class RegistrationOverview:
    total: int
    registered: int

    @property
    def unregistered(self) -> int:
        return self.total - self.registered


@dataclass(frozen=True, slots=True)
class AssessmentOverview:
    code: str
    label: str
    deadline_ts: int | None
    open_for_submissions: bool
    submitted: int
    missing: int
    reviewed: int
    awaiting_review: int


@dataclass(frozen=True, slots=True)
class LessonAttendanceGap:
    code: str
    title: str
    seminar_group: str | None
    expected: int
    marked: int
    state: AttendanceGapKind

    @property
    def unmarked(self) -> int:
        return self.expected - self.marked


@dataclass(frozen=True, slots=True)
class AttendanceOverview:
    ended_lessons: int
    fully_marked_lessons: int
    gaps: tuple[LessonAttendanceGap, ...]
    students_with_unknown_seminar_group: int

    @property
    def not_entered(self) -> tuple[LessonAttendanceGap, ...]:
        return tuple(
            gap for gap in self.gaps if gap.state is AttendanceGapKind.NOT_ENTERED
        )

    @property
    def incomplete(self) -> tuple[LessonAttendanceGap, ...]:
        return tuple(
            gap for gap in self.gaps if gap.state is AttendanceGapKind.INCOMPLETE
        )


@dataclass(frozen=True, slots=True)
class ActionSummary:
    unregistered: int
    awaiting_review: int
    lessons_without_attendance: int
    lessons_with_partial_attendance: int
    students_with_unknown_seminar_group: int

    @property
    def has_actions(self) -> bool:
        return bool(
            self.unregistered
            or self.awaiting_review
            or self.lessons_without_attendance
            or self.lessons_with_partial_attendance
            or self.students_with_unknown_seminar_group
        )


@dataclass(frozen=True, slots=True)
class CourseOverview:
    as_of_ts: int
    registration: RegistrationOverview
    assessments: tuple[AssessmentOverview, ...]
    attendance: AttendanceOverview
    actions: ActionSummary


def build_registration(students: Sequence[Student]) -> RegistrationOverview:
    registered = sum(1 for item in students if item.telegram_id is not None)
    return RegistrationOverview(total=len(students), registered=registered)


def needs_review(submission: Submission, grade: Grade | None) -> bool:
    return grade is None or submission.submitted_at > grade.graded_at


def is_operationally_relevant(
    assessment: Assessment, now: int, awaiting_review: int
) -> bool:
    if is_current(assessment, now):
        return True
    return (
        is_active_bot_work(assessment)
        and is_issued(assessment, now)
        and awaiting_review > 0
    )


def build_assessment_overview(
    assessment: Assessment,
    student_ids: frozenset[int],
    submissions: Mapping[int, Submission],
    grades: Mapping[int, Grade],
    now: int,
) -> AssessmentOverview:
    submitted = {
        student_id: submission
        for student_id, submission in submissions.items()
        if student_id in student_ids
    }
    awaiting = sum(
        1
        for student_id, submission in submitted.items()
        if needs_review(submission, grades.get(student_id))
    )
    return AssessmentOverview(
        code=assessment.code,
        label=assessment.label,
        deadline_ts=assessment.deadline_ts,
        open_for_submissions=is_accept_open(assessment, now),
        submitted=len(submitted),
        missing=len(student_ids) - len(submitted),
        reviewed=len(submitted) - awaiting,
        awaiting_review=awaiting,
    )


def lesson_cohort_ids(lesson: Lesson, students: Sequence[Student]) -> frozenset[int]:
    return frozenset(
        student.id
        for student in students
        if attends_lesson(lesson.kind, lesson.seminar_group, student.seminar_group)
    )


def build_attendance_gap(
    lesson: Lesson, students: Sequence[Student], marked_ids: frozenset[int]
) -> LessonAttendanceGap | None:
    cohort = lesson_cohort_ids(lesson, students)
    marked = cohort & marked_ids
    if len(marked) == len(cohort):
        return None
    state = (
        AttendanceGapKind.NOT_ENTERED if not marked else AttendanceGapKind.INCOMPLETE
    )
    return LessonAttendanceGap(
        code=lesson.code,
        title=lesson.title,
        seminar_group=lesson.seminar_group,
        expected=len(cohort),
        marked=len(marked),
        state=state,
    )


def build_attendance_overview(
    lessons: Sequence[Lesson],
    students: Sequence[Student],
    marks: Mapping[int, set[int]],
    now: int,
) -> AttendanceOverview:
    ended = [lesson for lesson in lessons if lesson.ends_ts <= now]
    gaps: list[LessonAttendanceGap] = []
    for lesson in ended:
        gap = build_attendance_gap(
            lesson, students, frozenset(marks.get(lesson.id, set()))
        )
        if gap is not None:
            gaps.append(gap)
    return AttendanceOverview(
        ended_lessons=len(ended),
        fully_marked_lessons=len(ended) - len(gaps),
        gaps=tuple(gaps),
        students_with_unknown_seminar_group=sum(
            1 for student in students if student.seminar_group is None
        ),
    )


def build_action_summary(
    registration: RegistrationOverview,
    assessments: Sequence[AssessmentOverview],
    attendance: AttendanceOverview,
) -> ActionSummary:
    return ActionSummary(
        unregistered=registration.unregistered,
        awaiting_review=sum(item.awaiting_review for item in assessments),
        lessons_without_attendance=len(attendance.not_entered),
        lessons_with_partial_attendance=len(attendance.incomplete),
        students_with_unknown_seminar_group=(
            attendance.students_with_unknown_seminar_group
        ),
    )


def _by_assessment[T](pairs: Mapping[tuple[int, int], T]) -> dict[int, dict[int, T]]:
    grouped: dict[int, dict[int, T]] = {}
    for (assessment_id, student_id), value in pairs.items():
        grouped.setdefault(assessment_id, {})[student_id] = value
    return grouped


async def build_course_overview(db: Database, now: int) -> CourseOverview:
    students = await db.list_students()
    lessons = await db.list_lessons()
    assessments = await db.list_assessments()
    submissions = _by_assessment(await db.latest_submissions_map())
    grades = _by_assessment(await db.grades_map())
    marks_by_lesson = await db.marked_student_ids_by_lesson()

    student_ids = frozenset(student.id for student in students)
    relevant: list[AssessmentOverview] = []
    for assessment in assessments:
        item = build_assessment_overview(
            assessment,
            student_ids,
            submissions.get(assessment.id, {}),
            grades.get(assessment.id, {}),
            now,
        )
        if is_operationally_relevant(assessment, now, item.awaiting_review):
            relevant.append(item)

    registration = build_registration(students)
    attendance = build_attendance_overview(lessons, students, marks_by_lesson, now)
    return CourseOverview(
        as_of_ts=now,
        registration=registration,
        assessments=tuple(relevant),
        attendance=attendance,
        actions=build_action_summary(registration, relevant, attendance),
    )
