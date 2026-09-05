from __future__ import annotations

from hwbot.course import DEFAULT_COURSE_PATH, load_course
from hwbot.formatting import format_grade_report, homework_status_for_student
from hwbot.grading import StudentState, build_report
from hwbot.models import Assessment, Submission
from hwbot.timeutil import parse_deadline


def _hw(deadline: int, accept_until: int | None = None) -> Assessment:
    close = deadline + 7 * 86400 if accept_until is None else accept_until
    return Assessment(
        id=1,
        code="hw1",
        label="ДЗ-1",
        title="ДЗ 1",
        body="body",
        component="homework",
        weight_final=0.0625,
        submit_via_bot=True,
        issued_at=1,
        deadline_ts=deadline,
        accept_until_ts=close,
        graded_on_ts=None,
        late_rule="homework",
        blocking=False,
        active=True,
    )


def test_status_labels() -> None:
    homework = _hw(100, accept_until=200)
    assert homework_status_for_student(homework, None, 50) == "не сдано"
    assert "просрочено" in homework_status_for_student(homework, None, 101)
    assert homework_status_for_student(homework, None, 201) == "приём закрыт · 0"
    submitted = Submission(1, 1, 1, "https://github.com/x", 40)
    assert homework_status_for_student(homework, submitted, 200) == "сдано"


def test_grade_report_november_text() -> None:
    course = load_course(DEFAULT_COURSE_PATH)
    now = parse_deadline("2026-11-21 16:00")
    held = [
        lesson.code
        for lesson in course.lessons
        if lesson.starts_ts <= now
        and (lesson.kind == "lecture" or lesson.seminar_group == "Б")
    ]
    absent = {held[0], held[1]}
    hw2 = course.assessment_by_code("hw2")
    assert hw2.deadline_ts is not None
    state = StudentState(
        seminar_group="Б",
        attendance={code: ("absent" if code in absent else "present") for code in held},
        held_lesson_codes=frozenset(held),
        submissions={
            "hw1": course.assessment_by_code("hw1").deadline_ts or 0,
            "hw2": hw2.deadline_ts + 300,
            "hw3": now,
            "project1": course.assessment_by_code("project1").deadline_ts or 0,
        },
        grades={"quiz1": 8, "quiz2": 7, "hw1": 9, "hw2": 10, "project1": 8},
    )
    text = format_grade_report(build_report(state, course, now), course)
    assert "Оценка на 21 ноября" in text
    assert "Идёшь на 8,1 из 10" in text
    assert "В кармане 3,8 из 7" in text
    assert "Если дальше ничего не сдавать: 3,0 — экзамен блокирующий" in text
    assert "срезано до 9,0" in text or "срезано до 9" in text
    assert "ждёт проверки" in text


def test_empty_grade_text() -> None:
    course = load_course(DEFAULT_COURSE_PATH)
    report = build_report(
        StudentState(None, {}, frozenset(), {}, {}),
        course,
        parse_deadline("2026-09-06 12:00"),
    )
    text = format_grade_report(report, course)
    assert "Пока нечего считать" in text
    assert "Семинарская группа не указана" in text
