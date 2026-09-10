from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum

from hwbot.course import Assessment as CourseAssessment
from hwbot.course import Course
from hwbot.course import Lesson as CourseLesson
from hwbot.models import Assessment, Lesson

FieldValue = str | int | float | bool | None


class EntityKind(Enum):
    LESSON = "lesson"
    ASSESSMENT = "assessment"


class ReconcileState(Enum):
    CREATE = "create"
    UPDATE = "update"
    UNCHANGED = "unchanged"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class FieldChange:
    field: str
    before: FieldValue
    after: FieldValue


@dataclass(frozen=True, slots=True)
class EntityPlan:
    kind: EntityKind
    code: str
    state: ReconcileState
    changes: tuple[FieldChange, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class CourseSnapshot:
    lessons: Mapping[str, Lesson]
    assessments: Mapping[str, Assessment]


@dataclass(frozen=True, slots=True)
class CoursePlan:
    entries: tuple[EntityPlan, ...]

    def select(
        self, kind: EntityKind, state: ReconcileState
    ) -> tuple[EntityPlan, ...]:
        return tuple(
            item for item in self.entries if item.kind is kind and item.state is state
        )

    def count(self, kind: EntityKind, state: ReconcileState) -> int:
        return len(self.select(kind, state))

    @property
    def created_lessons(self) -> int:
        return self.count(EntityKind.LESSON, ReconcileState.CREATE)

    @property
    def updated_lessons(self) -> int:
        return self.count(EntityKind.LESSON, ReconcileState.UPDATE)

    @property
    def unchanged_lessons(self) -> int:
        return self.count(EntityKind.LESSON, ReconcileState.UNCHANGED)

    @property
    def stale_lessons(self) -> int:
        return self.count(EntityKind.LESSON, ReconcileState.STALE)

    @property
    def created_assessments(self) -> int:
        return self.count(EntityKind.ASSESSMENT, ReconcileState.CREATE)

    @property
    def updated_assessments(self) -> int:
        return self.count(EntityKind.ASSESSMENT, ReconcileState.UPDATE)

    @property
    def unchanged_assessments(self) -> int:
        return self.count(EntityKind.ASSESSMENT, ReconcileState.UNCHANGED)

    @property
    def stale_assessments(self) -> int:
        return self.count(EntityKind.ASSESSMENT, ReconcileState.STALE)

    @property
    def stale(self) -> tuple[EntityPlan, ...]:
        return tuple(
            item for item in self.entries if item.state is ReconcileState.STALE
        )

    @property
    def has_stale(self) -> bool:
        return bool(self.stale)

    @property
    def writes(self) -> tuple[EntityPlan, ...]:
        return tuple(
            item
            for item in self.entries
            if item.state in {ReconcileState.CREATE, ReconcileState.UPDATE}
        )


def lesson_changes(
    existing: Lesson, desired: CourseLesson
) -> tuple[FieldChange, ...]:
    pairs: tuple[tuple[str, FieldValue, FieldValue], ...] = (
        ("kind", existing.kind, desired.kind),
        ("seminar_group", existing.seminar_group, desired.seminar_group),
        ("topic", existing.topic, desired.topic),
        ("title", existing.title, desired.title),
        ("starts_ts", existing.starts_ts, desired.starts_ts),
        ("ends_ts", existing.ends_ts, desired.ends_ts),
        ("room", existing.room, desired.room),
        ("module", existing.module, desired.module),
    )
    return tuple(
        FieldChange(name, before, after)
        for name, before, after in pairs
        if before != after
    )


def assessment_changes(
    existing: Assessment, desired: CourseAssessment
) -> tuple[FieldChange, ...]:
    pairs: tuple[tuple[str, FieldValue, FieldValue], ...] = (
        ("label", existing.label, desired.label),
        ("title", existing.title, desired.title),
        ("body", existing.body, desired.summary),
        ("component", existing.component, desired.component),
        ("weight_final", existing.weight_final, desired.weight_final),
        ("submit_via_bot", existing.submit_via_bot, desired.submit_via_bot),
        ("issued_at", existing.issued_at, desired.issued_at),
        ("deadline_ts", existing.deadline_ts, desired.deadline_ts),
        ("accept_until_ts", existing.accept_until_ts, desired.accept_until_ts),
        ("graded_on_ts", existing.graded_on_ts, desired.graded_on_ts),
        ("late_rule", existing.late_rule, desired.late_rule),
        ("blocking", existing.blocking, desired.blocking),
        ("active", existing.active, True),
    )
    return tuple(
        FieldChange(name, before, after)
        for name, before, after in pairs
        if before != after
    )


def _entry_for_desired(
    kind: EntityKind, code: str, changes: tuple[FieldChange, ...], known: bool
) -> EntityPlan:
    if not known:
        return EntityPlan(kind=kind, code=code, state=ReconcileState.CREATE)
    if changes:
        return EntityPlan(
            kind=kind, code=code, state=ReconcileState.UPDATE, changes=changes
        )
    return EntityPlan(kind=kind, code=code, state=ReconcileState.UNCHANGED)


def _reconcile_lessons(
    desired: Sequence[CourseLesson], existing: Mapping[str, Lesson]
) -> list[EntityPlan]:
    entries: list[EntityPlan] = []
    for lesson in desired:
        found = existing.get(lesson.code)
        changes = () if found is None else lesson_changes(found, lesson)
        entries.append(
            _entry_for_desired(
                EntityKind.LESSON, lesson.code, changes, found is not None
            )
        )
    desired_codes = {lesson.code for lesson in desired}
    for code in sorted(existing):
        if code not in desired_codes:
            entries.append(
                EntityPlan(
                    kind=EntityKind.LESSON, code=code, state=ReconcileState.STALE
                )
            )
    return entries


def _reconcile_assessments(
    desired: Sequence[CourseAssessment], existing: Mapping[str, Assessment]
) -> list[EntityPlan]:
    entries: list[EntityPlan] = []
    for assessment in desired:
        found = existing.get(assessment.code)
        changes = () if found is None else assessment_changes(found, assessment)
        entries.append(
            _entry_for_desired(
                EntityKind.ASSESSMENT, assessment.code, changes, found is not None
            )
        )
    desired_codes = {assessment.code for assessment in desired}
    for code in sorted(existing):
        if code in desired_codes or not existing[code].active:
            continue
        entries.append(
            EntityPlan(
                kind=EntityKind.ASSESSMENT, code=code, state=ReconcileState.STALE
            )
        )
    return entries


def reconcile_course(course: Course, snapshot: CourseSnapshot) -> CoursePlan:
    entries = _reconcile_lessons(course.lessons, snapshot.lessons)
    entries.extend(_reconcile_assessments(course.assessments, snapshot.assessments))
    return CoursePlan(tuple(entries))
