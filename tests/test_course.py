from __future__ import annotations

from pathlib import Path

import pytest

from datetime import datetime

from hwbot.config import PROJECT_ROOT
from hwbot.course import DEFAULT_COURSE_PATH, SEMINAR_WEEKDAY, load_course
from hwbot.errors import CourseError
from hwbot.timeutil import zone


def test_load_real_course() -> None:
    course = load_course(DEFAULT_COURSE_PATH)
    assert course.code == "python-ba-2026"
    assert len(course.lessons) == 36
    lectures = [item for item in course.lessons if item.kind == "lecture"]
    seminars_a = [
        item
        for item in course.lessons
        if item.kind == "seminar" and item.seminar_group == "261"
    ]
    seminars_b = [
        item
        for item in course.lessons
        if item.kind == "seminar" and item.seminar_group == "262"
    ]
    assert len(lectures) == 12
    assert len(seminars_a) == 12
    assert len(seminars_b) == 12
    assert len(course.assessments) == 11
    weight_sum = sum(item.weight for item in course.components)
    assert abs(weight_sum - 1.0) < 1e-9
    accumulated = sum(item.weight for item in course.components if item.in_accumulated)
    assert abs(accumulated - 0.70) < 1e-9
    for component in course.components:
        if component.computed_from != "assessments":
            continue
        item_sum = sum(
            item.weight_final for item in course.assessments_for(component.key)
        )
        assert abs(item_sum - component.weight) < 1e-9
    hw1 = course.assessment_by_code("hw1")
    assert hw1.deadline_ts is not None
    assert hw1.accept_until_ts is not None
    assert hw1.issued_at is not None
    assert hw1.issued_at < hw1.deadline_ts < hw1.accept_until_ts
    tz = zone(course.timezone)
    for lesson in course.lessons:
        if lesson.kind != "seminar" or lesson.seminar_group is None:
            continue
        weekday = datetime.fromtimestamp(lesson.starts_ts, tz).weekday()
        assert weekday == SEMINAR_WEEKDAY[lesson.seminar_group]
    exam = course.assessment_by_code("exam")
    for group, ts in exam.defense_by_group:
        assert datetime.fromtimestamp(ts, tz).weekday() == SEMINAR_WEEKDAY[group]


def test_broken_weight_sum(tmp_path: Path) -> None:
    text = (PROJECT_ROOT / "data" / "course.toml").read_text(encoding="utf-8")
    broken = text.replace(
        "[components.homework]\nweight = 0.25",
        "[components.homework]\nweight = 0.26",
    )
    path = tmp_path / "broken.toml"
    path.write_text(broken, encoding="utf-8")
    with pytest.raises(CourseError, match="Сумма весов компонентов"):
        load_course(path)


def test_unknown_late_rule(tmp_path: Path) -> None:
    text = (PROJECT_ROOT / "data" / "course.toml").read_text(encoding="utf-8")
    broken = text.replace('late_rule = "homework"', 'late_rule = "mystery"', 1)
    path = tmp_path / "broken.toml"
    path.write_text(broken, encoding="utf-8")
    with pytest.raises(CourseError, match="неизвестное правило просрочки"):
        load_course(path)


def test_wrong_seminar_weekday_is_rejected(tmp_path: Path) -> None:
    text = (PROJECT_ROOT / "data" / "course.toml").read_text(encoding="utf-8")
    broken = text.replace(
        'code = "S01B"\nkind = "seminar"\nseminar_group = "262"\n'
        'topic = 1\ntitle = "Знакомство с данными ПРАЙМ и учебным контуром"\n'
        "date = 2026-09-09",
        'code = "S01B"\nkind = "seminar"\nseminar_group = "262"\n'
        'topic = 1\ntitle = "Знакомство с данными ПРАЙМ и учебным контуром"\n'
        "date = 2026-09-12",
        1,
    )
    path = tmp_path / "broken.toml"
    path.write_text(broken, encoding="utf-8")
    with pytest.raises(CourseError, match="S01B"):
        load_course(path)


def test_duplicate_lesson_code(tmp_path: Path) -> None:
    text = (PROJECT_ROOT / "data" / "course.toml").read_text(encoding="utf-8")
    broken = text.replace('code = "S01A"', 'code = "L01"', 1)
    path = tmp_path / "broken.toml"
    path.write_text(broken, encoding="utf-8")
    with pytest.raises(CourseError, match="Коды занятий должны быть уникальны"):
        load_course(path)
