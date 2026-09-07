from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

from hwbot.course import Assessment, AttendanceScale, Course, LateRule, Lesson
from hwbot.groups import attends_lesson


class Mode(Enum):
    NOW = "now"
    FILL_ZERO = "fill_zero"


class ItemStatus(Enum):
    GRADED = "graded"
    AWAITING = "awaiting"
    OPEN = "open"
    MISSED = "missed"
    UPCOMING = "upcoming"


@dataclass(frozen=True, slots=True)
class ItemResult:
    code: str
    label: str
    component: str
    weight_final: float
    status: ItemStatus
    raw_score: float | None
    cap: float | None
    applied_score: float | None
    days_late: int
    submitted_at: int | None


@dataclass(frozen=True, slots=True)
class ComponentLine:
    key: str
    title: str
    weight: float
    score_now: float | None
    items: tuple[ItemResult, ...]


@dataclass(frozen=True, slots=True)
class StudentState:
    seminar_group: str | None
    attendance: Mapping[str, str]
    held_lesson_codes: frozenset[str]
    submissions: Mapping[str, int]
    grades: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class GradeReport:
    heading_to: float | None
    in_pocket: float | None
    if_nothing: float | None
    exam_blocked: bool
    as_of_ts: int
    seminar_group: str | None
    attendance_present: int
    attendance_absent: int
    attendance_excused: int
    attendance_held: int
    attendance_forecast_present: int
    attendance_forecast_absent: int
    attendance_forecast_excused: int
    attendance_score_now: float | None
    attendance_percent_now: float | None
    attendance_score_forecast: float | None
    components: tuple[ComponentLine, ...]
    items: tuple[ItemResult, ...]


def round_half_up(value: float, digits: int = 0) -> float:
    quant = Decimal(1).scaleb(-digits)
    return float(Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP))


def attendance_percent(present: int, absent: int, excused: int) -> float | None:
    _ = excused
    denominator = present + absent
    if denominator == 0:
        return None
    return round_half_up(100.0 * present / denominator, 0)


def attendance_score(
    present: int, absent: int, excused: int, scale: AttendanceScale
) -> float | None:
    denominator = present + absent
    if denominator == 0:
        return None
    if present == 0 and scale.zero_if_nothing_attended:
        return 0.0
    percent = attendance_percent(present, absent, excused)
    assert percent is not None
    for step in scale.steps:
        if percent >= step.min_percent:
            return float(step.score)
    return float(scale.steps[-1].score)


def days_late(submitted_at: int, deadline_ts: int) -> int:
    if submitted_at <= deadline_ts:
        return 0
    return max(0, math.ceil((submitted_at - deadline_ts) / 86400))


def late_cap(rule: LateRule, days: int) -> float:
    if days <= 0:
        return 10.0
    if rule.zero_after_days > 0 and days > rule.zero_after_days:
        return 0.0
    return max(10.0 - rule.per_day * days, rule.floor)


def apply_late(score: float, cap: float) -> float:
    return min(score, cap)


def component_score(items: Sequence[ItemResult], mode: Mode) -> float | None:
    if mode is Mode.NOW:
        counted = [
            item
            for item in items
            if item.status in {ItemStatus.GRADED, ItemStatus.MISSED}
        ]
        if not counted:
            return None
        weight_sum = sum(item.weight_final for item in counted)
        if weight_sum == 0:
            return None
        total = 0.0
        for item in counted:
            score = 0.0 if item.applied_score is None else item.applied_score
            total += item.weight_final * score
        return total / weight_sum
    weight_sum = sum(item.weight_final for item in items)
    if weight_sum == 0:
        return None
    total = 0.0
    for item in items:
        score = 0.0 if item.applied_score is None else item.applied_score
        total += item.weight_final * score
    return total / weight_sum


def accumulated(scores: Mapping[str, float], course: Course) -> float:
    total = 0.0
    for component in course.components:
        if not component.in_accumulated:
            continue
        total += component.weight * scores.get(component.key, 0.0)
    return min(total, course.accumulated_cap)


def final_score(acc: float, exam: float | None, course: Course) -> float | None:
    if exam is None:
        return None
    result = acc + course.component("exam").weight * exam
    if exam < course.exam_blocking_threshold:
        result = min(result, course.final_cap_when_blocked)
    return result


def student_lessons(course: Course, seminar_group: str | None) -> tuple[Lesson, ...]:
    return tuple(
        lesson
        for lesson in course.lessons
        if attends_lesson(lesson.kind, lesson.seminar_group, seminar_group)
    )


def count_attendance(
    lessons: Sequence[Lesson],
    marks: Mapping[str, str],
    *,
    held_codes: frozenset[str] | None,
    missing_as_absent: bool,
) -> tuple[int, int, int]:
    present = 0
    absent = 0
    excused = 0
    for lesson in lessons:
        if held_codes is not None and lesson.code not in held_codes:
            continue
        status = marks.get(lesson.code)
        if status == "present":
            present += 1
        elif status == "excused":
            excused += 1
        elif status == "absent":
            absent += 1
        elif missing_as_absent:
            absent += 1
    return present, absent, excused


def item_status(
    assessment: Assessment,
    now: int,
    submitted_at: int | None,
    has_grade: bool,
) -> ItemStatus:
    if has_grade:
        return ItemStatus.GRADED
    if not assessment.submit_via_bot:
        if assessment.graded_on_ts is None or now < assessment.graded_on_ts:
            return ItemStatus.UPCOMING
        return ItemStatus.AWAITING
    if submitted_at is not None:
        return ItemStatus.AWAITING
    if assessment.issued_at is not None and now < assessment.issued_at:
        return ItemStatus.UPCOMING
    if assessment.accept_until_ts is not None and now > assessment.accept_until_ts:
        return ItemStatus.MISSED
    if assessment.issued_at is None and assessment.deadline_ts is not None and now < assessment.deadline_ts:
        return ItemStatus.UPCOMING
    return ItemStatus.OPEN


def build_item(
    assessment: Assessment,
    course: Course,
    now: int,
    submitted_at: int | None,
    raw_score: float | None,
) -> ItemResult:
    status = item_status(assessment, now, submitted_at, raw_score is not None)
    late_days = 0
    cap: float | None = None
    applied: float | None = None
    rule = course.late_rule_named(assessment.late_rule)
    if submitted_at is not None and assessment.deadline_ts is not None:
        late_days = days_late(submitted_at, assessment.deadline_ts)
        cap = late_cap(rule, late_days)
    elif raw_score is not None and assessment.deadline_ts is not None:
        late_days = 0
        cap = late_cap(rule, 0)
    if raw_score is not None:
        cap = late_cap(rule, late_days) if cap is None else cap
        applied = apply_late(raw_score, cap)
    return ItemResult(
        code=assessment.code,
        label=assessment.label,
        component=assessment.component,
        weight_final=assessment.weight_final,
        status=status,
        raw_score=raw_score,
        cap=cap,
        applied_score=applied,
        days_late=late_days,
        submitted_at=submitted_at,
    )


def build_report(state: StudentState, course: Course, now: int) -> GradeReport:
    lessons = student_lessons(course, state.seminar_group)
    present, absent, excused = count_attendance(
        lessons,
        state.attendance,
        held_codes=state.held_lesson_codes,
        missing_as_absent=True,
    )
    forecast_present, forecast_absent, forecast_excused = count_attendance(
        lessons,
        state.attendance,
        held_codes=None,
        missing_as_absent=True,
    )
    att_now = attendance_score(present, absent, excused, course.attendance_scale)
    att_percent = attendance_percent(present, absent, excused)
    att_forecast = attendance_score(
        forecast_present,
        forecast_absent,
        forecast_excused,
        course.attendance_scale,
    )
    items = tuple(
        build_item(
            assessment,
            course,
            now,
            state.submissions.get(assessment.code),
            state.grades.get(assessment.code),
        )
        for assessment in course.assessments
    )
    component_lines: list[ComponentLine] = []
    now_scores: dict[str, float] = {}
    pocket_scores: dict[str, float] = {}
    forecast_scores: dict[str, float] = {}
    if att_now is not None:
        now_scores["attendance"] = att_now
        pocket_scores["attendance"] = att_now
    if att_forecast is not None:
        forecast_scores["attendance"] = att_forecast
    for component in course.components:
        if component.computed_from != "assessments":
            component_lines.append(
                ComponentLine(
                    key=component.key,
                    title=component.title,
                    weight=component.weight,
                    score_now=att_now if component.key == "attendance" else None,
                    items=(),
                )
            )
            continue
        group = tuple(item for item in items if item.component == component.key)
        score_now = component_score(group, Mode.NOW)
        score_zero = component_score(group, Mode.FILL_ZERO)
        if score_now is not None:
            now_scores[component.key] = score_now
        if score_zero is not None:
            pocket_scores[component.key] = score_zero
            forecast_scores[component.key] = score_zero
        component_lines.append(
            ComponentLine(
                key=component.key,
                title=component.title,
                weight=component.weight,
                score_now=score_now,
                items=group,
            )
        )
    heading = _heading_to(now_scores, course)
    empty = not now_scores
    pocket = None if empty else accumulated(pocket_scores, course)
    exam_item = next((item for item in items if item.component == "exam"), None)
    exam_now = None if exam_item is None else exam_item.applied_score
    exam_for_forecast = 0.0 if exam_now is None else exam_now
    forecast_acc = accumulated(forecast_scores, course)
    forecast = None if empty else final_score(forecast_acc, exam_for_forecast, course)
    exam_blocked = (
        not empty
        and forecast is not None
        and exam_for_forecast < course.exam_blocking_threshold
    )
    return GradeReport(
        heading_to=heading,
        in_pocket=pocket,
        if_nothing=forecast,
        exam_blocked=exam_blocked,
        as_of_ts=now,
        seminar_group=state.seminar_group,
        attendance_present=present,
        attendance_absent=absent,
        attendance_excused=excused,
        attendance_held=present + absent + excused,
        attendance_forecast_present=forecast_present,
        attendance_forecast_absent=forecast_absent,
        attendance_forecast_excused=forecast_excused,
        attendance_score_now=att_now,
        attendance_percent_now=att_percent,
        attendance_score_forecast=att_forecast,
        components=tuple(component_lines),
        items=items,
    )


def _heading_to(scores: Mapping[str, float], course: Course) -> float | None:
    if not scores:
        return None
    weight_sum = sum(course.component(key).weight for key in scores)
    if weight_sum == 0:
        return None
    total = sum(course.component(key).weight * score for key, score in scores.items())
    return total / weight_sum
