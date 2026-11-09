from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
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


# Исход известен: проверено или не сдано до закрытия приёма (это ноль).
# Сданное и непроверенное сюда не входит — ни в «сейчас», ни в «можно было набрать».
COUNTED = frozenset({ItemStatus.GRADED, ItemStatus.MISSED})


@dataclass(frozen=True, slots=True)
class ItemResult:
    code: str
    label: str
    component: str
    weight_final: float
    status: ItemStatus
    raw_score: float | None
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
    # Баллы из 10 по проверенному и сколько по нему можно было набрать; None — проверять нечего.
    points_now: float | None = None
    points_possible: float | None = None


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
    # «Сейчас N из 10» и «к этому дню можно было набрать M» — только по проверенному.
    points_now: float | None = None
    points_possible: float | None = None


def round_half_up(value: float, digits: int = 0) -> float:
    quant = Decimal(1).scaleb(-digits)
    return float(Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP))


def round_parts(parts: Sequence[Decimal], digits: int = 2) -> tuple[list[float], float]:
    """Округлить слагаемые так, чтобы они сложились ровно в округлённую сумму.

    Метод наибольших остатков. Без него 0,375 + 1,125 показались бы как 0,38 + 1,13 = 1,51
    при сумме 1,50, и в разбивке нашлась бы лишняя сотая.
    """
    unit = Decimal(1).scaleb(-digits)
    total = sum(parts, Decimal(0)).quantize(unit, rounding=ROUND_HALF_UP)
    rounded = [part.quantize(unit, rounding=ROUND_FLOOR) for part in parts]
    rest = int((total - sum(rounded, Decimal(0))) / unit)
    by_remainder = sorted(
        range(len(parts)), key=lambda i: parts[i] - rounded[i], reverse=True
    )
    for i in by_remainder[:rest]:
        rounded[i] += unit
    return [float(value) for value in rounded], float(total)


def _dec(value: float) -> Decimal:
    return Decimal(str(value))


def counted_points(
    items: Sequence[ItemResult], score_max: float
) -> tuple[Decimal, Decimal] | None:
    """Баллы из 10 за элементы с известным исходом и сколько за них можно было набрать."""
    counted = [item for item in items if item.status in COUNTED]
    if not counted:
        return None
    now = sum(
        (_dec(item.weight_final) * _dec(item.applied_score or 0.0) for item in counted),
        Decimal(0),
    )
    possible = sum((_dec(item.weight_final) * _dec(score_max) for item in counted), Decimal(0))
    return now, possible


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


def late_penalty(rule: LateRule, days: int) -> float | None:
    """Сколько баллов вычесть за `days` начатых суток просрочки.

    None — приём уже обнулился (days > zero_after_days); zero_after_days = 0 значит «никогда».
    """
    if days <= 0:
        return 0.0
    if rule.zero_after_days > 0 and days > rule.zero_after_days:
        return None
    return rule.per_day * days


def apply_late(rule: LateRule, raw: float, days: int) -> float:
    """Итог = max(raw − per_day·d, min(raw, floor)); 0, если приём обнулился.

    Штраф не опускает оценку ниже floor, но и не поднимает ту, что уже ниже floor.
    """
    penalty = late_penalty(rule, days)
    if penalty is None:
        return 0.0
    minus = float(Decimal(str(raw)) - Decimal(str(penalty)))
    return max(minus, min(raw, rule.floor))


def component_score(items: Sequence[ItemResult], mode: Mode) -> float | None:
    if mode is Mode.NOW:
        counted = [item for item in items if item.status in COUNTED]
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
    applied: float | None = None
    rule = course.late_rule_named(assessment.late_rule)
    if submitted_at is not None and assessment.deadline_ts is not None:
        late_days = days_late(submitted_at, assessment.deadline_ts)
    if raw_score is not None:
        applied = apply_late(rule, raw_score, late_days)
    return ItemResult(
        code=assessment.code,
        label=assessment.label,
        component=assessment.component,
        weight_final=assessment.weight_final,
        status=status,
        raw_score=raw_score,
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
    exact: dict[str, tuple[Decimal, Decimal]] = {}
    for line in component_lines:
        if line.key == "attendance":
            if att_now is not None:
                weight = _dec(line.weight)
                exact[line.key] = (weight * _dec(att_now), weight * _dec(course.score_max))
            continue
        points = counted_points(line.items, course.score_max)
        if points is not None:
            exact[line.key] = points
    points_now: float | None = None
    points_possible: float | None = None
    if exact:
        keys = list(exact)
        now_parts, points_now = round_parts([exact[key][0] for key in keys])
        possible_parts, points_possible = round_parts([exact[key][1] for key in keys])
        by_key = dict(zip(keys, zip(now_parts, possible_parts, strict=True), strict=True))
        component_lines = [
            replace(line, points_now=by_key[line.key][0], points_possible=by_key[line.key][1])
            if line.key in by_key
            else line
            for line in component_lines
        ]
        exam_known = exam_item is not None and exam_item.status in COUNTED
        if exam_known and exam_for_forecast < course.exam_blocking_threshold:
            points_now = min(points_now, course.final_cap_when_blocked)
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
        points_now=points_now,
        points_possible=points_possible,
    )


def _heading_to(scores: Mapping[str, float], course: Course) -> float | None:
    if not scores:
        return None
    weight_sum = sum(course.component(key).weight for key in scores)
    if weight_sum == 0:
        return None
    total = sum(course.component(key).weight * score for key, score in scores.items())
    return total / weight_sum
