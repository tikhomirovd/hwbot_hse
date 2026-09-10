from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import cast

from hwbot.config import PROJECT_ROOT
from hwbot.errors import CourseError
from hwbot.timeutil import parse_deadline, parse_local_date_time, zone

DEFAULT_COURSE_PATH = PROJECT_ROOT / "data" / "course.toml"
WEIGHT_TOLERANCE = 1e-9
# 261 занимается по субботам, 262 — по средам. datetime.weekday(): Mon=0.
SEMINAR_WEEKDAY = {"261": 5, "262": 2}
_WEEKDAY_RU = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)


@dataclass(frozen=True, slots=True)
class AttendanceStep:
    min_percent: int
    score: int


@dataclass(frozen=True, slots=True)
class AttendanceScale:
    zero_if_nothing_attended: bool
    late_arrival_minutes_counts_as_absent: int
    steps: tuple[AttendanceStep, ...]


@dataclass(frozen=True, slots=True)
class LateRule:
    name: str
    per_day: float
    floor: float
    zero_after_days: int
    floor_within_days: int | None


@dataclass(frozen=True, slots=True)
class Component:
    key: str
    weight: float
    title: str
    in_accumulated: bool
    computed_from: str
    blocking: bool


@dataclass(frozen=True, slots=True)
class Criterion:
    title: str
    share: float


@dataclass(frozen=True, slots=True)
class Lesson:
    code: str
    kind: str
    seminar_group: str | None
    topic: int
    title: str
    starts_ts: int
    ends_ts: int
    room: str | None
    module: int
    note: str | None


@dataclass(frozen=True, slots=True)
class Assessment:
    code: str
    label: str
    title: str
    summary: str
    component: str
    weight_final: float
    submit_via_bot: bool
    issued_at: int | None
    deadline_ts: int | None
    accept_until_ts: int | None
    graded_on_ts: int | None
    late_rule: str
    blocking: bool
    lesson: str | None
    duration_minutes: int | None
    defense_from_ts: int | None
    defense_to_ts: int | None
    defense_by_group: tuple[tuple[str, int], ...]
    criteria: tuple[Criterion, ...]


@dataclass(frozen=True, slots=True)
class Course:
    code: str
    title: str
    programme: str
    year: str
    timezone: str
    lessons_per_student: int
    accumulated_cap: float
    exam_blocking_threshold: float
    final_cap_when_blocked: float
    score_min: float
    score_max: float
    components: tuple[Component, ...]
    attendance_scale: AttendanceScale
    late_rules: tuple[LateRule, ...]
    lessons: tuple[Lesson, ...]
    assessments: tuple[Assessment, ...]

    def component(self, key: str) -> Component:
        for item in self.components:
            if item.key == key:
                return item
        raise CourseError(f"Неизвестный компонент: {key}")

    def late_rule_named(self, name: str) -> LateRule:
        for item in self.late_rules:
            if item.name == name:
                return item
        raise CourseError(f"Неизвестное правило просрочки: {name}")

    def assessment_by_code(self, code: str) -> Assessment:
        for item in self.assessments:
            if item.code == code:
                return item
        raise CourseError(f"Неизвестный элемент контроля: {code}")

    def lesson_by_code(self, code: str) -> Lesson:
        for item in self.lessons:
            if item.code == code:
                return item
        raise CourseError(f"Неизвестное занятие: {code}")

    def assessments_for(self, component_key: str) -> tuple[Assessment, ...]:
        return tuple(item for item in self.assessments if item.component == component_key)

    def defense_ts_for(self, assessment: Assessment, seminar_group: str | None) -> int | None:
        if seminar_group is None:
            return None
        for group, ts in assessment.defense_by_group:
            if group == seminar_group:
                return ts
        return None


def load_course(path: Path) -> Course:
    if not path.exists():
        raise CourseError(f"Нет файла курса: {path}")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise CourseError(f"Не разобрал course.toml: {exc}") from exc
    course = _parse_course(raw)
    _validate_course(course)
    return course


def _parse_course(raw: Mapping[str, object]) -> Course:
    meta = _table(raw.get("course"), "course")
    tz_name = _str(meta.get("timezone"), "course.timezone")
    components = _parse_components(_table(raw.get("components"), "components"))
    scale = _parse_scale(_table(raw.get("attendance_scale"), "attendance_scale"))
    late_rules = _parse_late_rules(_table(raw.get("late_rules"), "late_rules"))
    lessons = tuple(
        _parse_lesson(item, tz_name) for item in _object_list(raw.get("lessons"), "[[lessons]]")
    )
    assessments = tuple(
        _parse_assessment(item, tz_name)
        for item in _object_list(raw.get("assessments"), "[[assessments]]")
    )
    return Course(
        code=_str(meta.get("code"), "course.code"),
        title=_str(meta.get("title"), "course.title"),
        programme=_str(meta.get("programme"), "course.programme"),
        year=_str(meta.get("year"), "course.year"),
        timezone=tz_name,
        lessons_per_student=_int(meta.get("lessons_per_student"), "course.lessons_per_student"),
        accumulated_cap=_float(meta.get("accumulated_cap"), "course.accumulated_cap"),
        exam_blocking_threshold=_float(
            meta.get("exam_blocking_threshold"), "course.exam_blocking_threshold"
        ),
        final_cap_when_blocked=_float(
            meta.get("final_cap_when_blocked"), "course.final_cap_when_blocked"
        ),
        score_min=_float(meta.get("score_min"), "course.score_min"),
        score_max=_float(meta.get("score_max"), "course.score_max"),
        components=components,
        attendance_scale=scale,
        late_rules=late_rules,
        lessons=lessons,
        assessments=assessments,
    )


def _parse_components(raw: Mapping[str, object]) -> tuple[Component, ...]:
    items: list[Component] = []
    for key, value in raw.items():
        table = _table(value, f"components.{key}")
        items.append(
            Component(
                key=key,
                weight=_float(table.get("weight"), f"components.{key}.weight"),
                title=_str(table.get("title"), f"components.{key}.title"),
                in_accumulated=_bool(
                    table.get("in_accumulated"), f"components.{key}.in_accumulated"
                ),
                computed_from=_str(
                    table.get("computed_from"), f"components.{key}.computed_from"
                ),
                blocking=_optional_bool(table.get("blocking"), f"components.{key}.blocking"),
            )
        )
    if not items:
        raise CourseError("В course.toml нет компонентов оценки")
    return tuple(items)


def _parse_scale(raw: Mapping[str, object]) -> AttendanceScale:
    steps_raw = _object_list(raw.get("steps"), "attendance_scale.steps")
    steps: list[AttendanceStep] = []
    for index, item in enumerate(steps_raw):
        table = _table(item, f"attendance_scale.steps[{index}]")
        steps.append(
            AttendanceStep(
                min_percent=_int(
                    table.get("min_percent"), f"attendance_scale.steps[{index}].min_percent"
                ),
                score=_int(table.get("score"), f"attendance_scale.steps[{index}].score"),
            )
        )
    return AttendanceScale(
        zero_if_nothing_attended=_bool(
            raw.get("zero_if_nothing_attended"), "attendance_scale.zero_if_nothing_attended"
        ),
        late_arrival_minutes_counts_as_absent=_int(
            raw.get("late_arrival_minutes_counts_as_absent"),
            "attendance_scale.late_arrival_minutes_counts_as_absent",
        ),
        steps=tuple(steps),
    )


def _parse_late_rules(raw: Mapping[str, object]) -> tuple[LateRule, ...]:
    items: list[LateRule] = []
    for name, value in raw.items():
        table = _table(value, f"late_rules.{name}")
        items.append(
            LateRule(
                name=name,
                per_day=_float(table.get("per_day"), f"late_rules.{name}.per_day"),
                floor=_float(table.get("floor"), f"late_rules.{name}.floor"),
                zero_after_days=_int(
                    table.get("zero_after_days"), f"late_rules.{name}.zero_after_days"
                ),
                floor_within_days=None,
            )
        )
    if not items:
        raise CourseError("В course.toml нет правил просрочки")
    return tuple(items)


def _parse_lesson(raw: object, tz_name: str) -> Lesson:
    table = _table(raw, "lessons")
    code = _str(table.get("code"), "lessons.code")
    kind = _str(table.get("kind"), f"lessons.{code}.kind")
    seminar_group = _optional_str(table.get("seminar_group"), f"lessons.{code}.seminar_group")
    lesson_date = table.get("date")
    if not isinstance(lesson_date, date) or isinstance(lesson_date, datetime):
        raise CourseError(f"lessons.{code}.date должен быть датой")
    starts_ts = parse_local_date_time(
        lesson_date.isoformat(),
        _str(table.get("starts_at"), f"lessons.{code}.starts_at"),
        tz_name,
    )
    ends_ts = parse_local_date_time(
        lesson_date.isoformat(),
        _str(table.get("ends_at"), f"lessons.{code}.ends_at"),
        tz_name,
    )
    room = _optional_str(table.get("room"), f"lessons.{code}.room")
    note = _optional_str(table.get("note"), f"lessons.{code}.note")
    return Lesson(
        code=code,
        kind=kind,
        seminar_group=seminar_group,
        topic=_int(table.get("topic"), f"lessons.{code}.topic"),
        title=_str(table.get("title"), f"lessons.{code}.title"),
        starts_ts=starts_ts,
        ends_ts=ends_ts,
        room=room,
        module=_int(table.get("module"), f"lessons.{code}.module"),
        note=note,
    )


def _parse_assessment(raw: object, tz_name: str) -> Assessment:
    table = _table(raw, "assessments")
    code = _str(table.get("code"), "assessments.code")
    prefix = f"assessments.{code}"
    defense_raw = table.get("defense_by_group")
    defense_pairs: list[tuple[str, int]] = []
    if defense_raw is not None:
        defense_table = _table(defense_raw, f"{prefix}.defense_by_group")
        for group, value in defense_table.items():
            defense_pairs.append((group, _to_start_of_day_ts(value, tz_name, f"{prefix}.defense_by_group.{group}")))
    criteria_raw = table.get("criteria")
    criteria: list[Criterion] = []
    if criteria_raw is not None:
        for index, item in enumerate(_object_list(criteria_raw, f"{prefix}.criteria")):
            item_table = _table(item, f"{prefix}.criteria[{index}]")
            criteria.append(
                Criterion(
                    title=_str(item_table.get("title"), f"{prefix}.criteria[{index}].title"),
                    share=_float(item_table.get("share"), f"{prefix}.criteria[{index}].share"),
                )
            )
    return Assessment(
        code=code,
        label=_str(table.get("label"), f"{prefix}.label"),
        title=_str(table.get("title"), f"{prefix}.title"),
        summary=_optional_str(table.get("summary"), f"{prefix}.summary") or "",
        component=_str(table.get("component"), f"{prefix}.component"),
        weight_final=_float(table.get("weight_final"), f"{prefix}.weight_final"),
        submit_via_bot=_bool(table.get("submit_via_bot"), f"{prefix}.submit_via_bot"),
        issued_at=_optional_ts(table.get("issued_at"), tz_name, f"{prefix}.issued_at"),
        deadline_ts=_optional_ts(table.get("deadline"), tz_name, f"{prefix}.deadline"),
        accept_until_ts=_optional_ts(
            table.get("accept_until"), tz_name, f"{prefix}.accept_until"
        ),
        graded_on_ts=_optional_ts(table.get("graded_on"), tz_name, f"{prefix}.graded_on"),
        late_rule=_str(table.get("late_rule"), f"{prefix}.late_rule"),
        blocking=_optional_bool(table.get("blocking"), f"{prefix}.blocking"),
        lesson=_optional_str(table.get("lesson"), f"{prefix}.lesson"),
        duration_minutes=_optional_int(
            table.get("duration_minutes"), f"{prefix}.duration_minutes"
        ),
        defense_from_ts=_optional_ts(
            table.get("defense_from"), tz_name, f"{prefix}.defense_from"
        ),
        defense_to_ts=_optional_ts(table.get("defense_to"), tz_name, f"{prefix}.defense_to"),
        defense_by_group=tuple(defense_pairs),
        criteria=tuple(criteria),
    )


def _validate_course(course: Course) -> None:
    weight_sum = sum(item.weight for item in course.components)
    if abs(weight_sum - 1.0) > WEIGHT_TOLERANCE:
        raise CourseError(
            f"Сумма весов компонентов должна быть 1.00, сейчас {weight_sum:.12f}"
        )
    accumulated_weight = sum(
        item.weight for item in course.components if item.in_accumulated
    )
    if abs(accumulated_weight - 0.70) > WEIGHT_TOLERANCE:
        raise CourseError(
            "Сумма весов компонентов с in_accumulated должна быть 0.70, "
            f"сейчас {accumulated_weight:.12f}"
        )
    expected_cap = 10.0 * accumulated_weight
    if abs(course.accumulated_cap - expected_cap) > WEIGHT_TOLERANCE:
        raise CourseError(
            f"accumulated_cap должен быть равен 10 × 0.70 = {expected_cap:.1f}, "
            f"сейчас {course.accumulated_cap}"
        )
    for component in course.components:
        if component.computed_from != "assessments":
            continue
        items = course.assessments_for(component.key)
        item_sum = sum(item.weight_final for item in items)
        if abs(item_sum - component.weight) > WEIGHT_TOLERANCE:
            raise CourseError(
                f"Сумма weight_final в компоненте {component.key} должна быть "
                f"{component.weight}, сейчас {item_sum:.12f}"
            )
    lesson_codes = [item.code for item in course.lessons]
    if len(lesson_codes) != len(set(lesson_codes)):
        raise CourseError("Коды занятий должны быть уникальны")
    assessment_codes = [item.code for item in course.assessments]
    if len(assessment_codes) != len(set(assessment_codes)):
        raise CourseError("Коды элементов контроля должны быть уникальны")
    lectures = [item for item in course.lessons if item.kind == "lecture"]
    for group in ("261", "262"):
        seminars = [
            item
            for item in course.lessons
            if item.kind == "seminar" and item.seminar_group == group
        ]
        total = len(lectures) + len(seminars)
        if total != course.lessons_per_student:
            raise CourseError(
                f"Лекции + семинары группы {group} должны давать "
                f"{course.lessons_per_student} занятий, сейчас {total}"
            )
    _validate_seminar_weekdays(course)
    known_rules = {item.name for item in course.late_rules}
    known_components = {item.key for item in course.components}
    for assessment in course.assessments:
        if assessment.late_rule not in known_rules:
            raise CourseError(
                f"У элемента {assessment.code} неизвестное правило просрочки "
                f"{assessment.late_rule!r}"
            )
        if assessment.component not in known_components:
            raise CourseError(
                f"У элемента {assessment.code} неизвестный компонент "
                f"{assessment.component!r}"
            )
        if (
            assessment.issued_at is not None
            and assessment.deadline_ts is not None
            and not (assessment.issued_at < assessment.deadline_ts)
        ):
            raise CourseError(
                f"У элемента {assessment.code} issued_at должен быть раньше deadline"
            )
        if (
            assessment.deadline_ts is not None
            and assessment.accept_until_ts is not None
            and not (assessment.deadline_ts < assessment.accept_until_ts)
        ):
            raise CourseError(
                f"У элемента {assessment.code} deadline должен быть раньше accept_until"
            )
    _validate_scale(course.attendance_scale)
    _ = zone(course.timezone)


def _weekday_of(ts: int, tz_name: str) -> int:
    return datetime.fromtimestamp(ts, zone(tz_name)).weekday()


def _validate_seminar_weekdays(course: Course) -> None:
    for lesson in course.lessons:
        if lesson.kind != "seminar" or lesson.seminar_group is None:
            continue
        expected = SEMINAR_WEEKDAY.get(lesson.seminar_group)
        if expected is None:
            continue
        actual = _weekday_of(lesson.starts_ts, course.timezone)
        if actual != expected:
            raise CourseError(
                f"Семинар {lesson.code} группы {lesson.seminar_group} должен быть "
                f"в {_WEEKDAY_RU[expected]}, сейчас {_WEEKDAY_RU[actual]}"
            )
    for assessment in course.assessments:
        for group, ts in assessment.defense_by_group:
            expected = SEMINAR_WEEKDAY.get(group)
            if expected is None:
                continue
            actual = _weekday_of(ts, course.timezone)
            if actual != expected:
                raise CourseError(
                    f"Защита {assessment.code} группы {group} должна быть "
                    f"в {_WEEKDAY_RU[expected]}, сейчас {_WEEKDAY_RU[actual]}"
                )


def _validate_scale(scale: AttendanceScale) -> None:
    if not scale.steps:
        raise CourseError("Шкала посещаемости пуста")
    percents = [step.min_percent for step in scale.steps]
    if percents != sorted(percents, reverse=True):
        raise CourseError("Ступени шкалы посещаемости должны идти по убыванию процента")
    scores = [step.score for step in scale.steps]
    expected = list(range(10, 10 - len(scores), -1))
    if scores != expected:
        raise CourseError("Баллы ступеней шкалы посещаемости должны идти от 10 к 1")


def _optional_ts(raw: object, tz_name: str, field: str) -> int | None:
    if raw is None:
        return None
    return _to_ts(raw, tz_name, field)


def _to_ts(raw: object, tz_name: str, field: str) -> int:
    if isinstance(raw, datetime):
        localized = raw if raw.tzinfo is not None else raw.replace(tzinfo=zone(tz_name))
        return int(localized.timestamp())
    if isinstance(raw, date):
        return _to_start_of_day_ts(raw, tz_name, field)
    if isinstance(raw, str):
        return parse_deadline(raw, tz_name)
    raise CourseError(f"{field} должен быть датой или датой-временем")


def _to_start_of_day_ts(raw: object, tz_name: str, field: str) -> int:
    if isinstance(raw, datetime):
        day = raw.date()
    elif isinstance(raw, date):
        day = raw
    elif isinstance(raw, str):
        return parse_deadline(f"{raw} 00:00", tz_name)
    else:
        raise CourseError(f"{field} должен быть датой")
    return parse_local_date_time(day.isoformat(), "00:00", tz_name)


def _object_list(raw: object, name: str) -> list[object]:
    if not isinstance(raw, list):
        raise CourseError(f"Ожидался массив {name}")
    values = cast(list[object], raw)
    items: list[object] = []
    for index in range(len(values)):
        items.append(values[index])
    return items


def _table(raw: object, name: str) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise CourseError(f"Ожидалась таблица [{name}]")
    return {str(key): value for key, value in cast(dict[object, object], raw).items()}


def _str(raw: object, field: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise CourseError(f"Поле {field} должно быть непустой строкой")
    return raw


def _optional_str(raw: object, field: str) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise CourseError(f"Поле {field} должно быть строкой")
    text = raw.strip()
    return text or None


def _int(raw: object, field: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise CourseError(f"Поле {field} должно быть целым числом")
    return raw


def _optional_int(raw: object, field: str) -> int | None:
    if raw is None:
        return None
    return _int(raw, field)


def _float(raw: object, field: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise CourseError(f"Поле {field} должно быть числом")
    return float(raw)


def _bool(raw: object, field: str) -> bool:
    if not isinstance(raw, bool):
        raise CourseError(f"Поле {field} должно быть true или false")
    return raw


def _optional_bool(raw: object, field: str) -> bool:
    if raw is None:
        return False
    return _bool(raw, field)
