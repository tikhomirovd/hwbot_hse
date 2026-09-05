from __future__ import annotations

from hwbot.course import DEFAULT_COURSE_PATH, load_course
from hwbot.formatting import (
    format_attendance_full_list,
    format_grade_report,
    format_homework_card,
    homework_status_for_student,
)
from hwbot.grading import StudentState, build_report, count_attendance, student_lessons
from hwbot.models import Assessment, Submission
from hwbot.telegramutil import escape_html
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
    assert homework_status_for_student(homework, None, 101) == "просрочено"
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
    assert "оценка на 21 ноября" in text.casefold() or "21 ноября" in text
    assert "8,1" in text
    assert "3,8" in text
    assert "остановиться" in text
    assert "срезано" in text
    assert "ждёт проверки" in text
    assert "<pre>" in text
    assert "перекличк" not in text
    assert "напиши преподавателю" not in text.casefold()


def test_empty_grade_text() -> None:
    course = load_course(DEFAULT_COURSE_PATH)
    report = build_report(
        StudentState(None, {}, frozenset(), {}, {}),
        course,
        parse_deadline("2026-09-06 12:00"),
    )
    text = format_grade_report(report, course)
    assert "считать пока нечего" in text.casefold()
    assert "перекличк" not in text
    assert "напиши преподавателю" not in text.casefold()


def test_payload_with_html_is_escaped_in_card() -> None:
    homework = _hw(1000)
    submission = Submission(1, 1, 1, "<b>oops</b>", 40)
    text = format_homework_card(homework, submission, 50)
    assert "<b>oops</b>" not in text
    assert escape_html("<b>oops</b>") in text or "&lt;b&gt;oops&lt;/b&gt;" in text


def test_attendance_matches_grade_counts() -> None:
    course = load_course(DEFAULT_COURSE_PATH)
    now = parse_deadline("2026-10-10 12:00")
    lessons = list(student_lessons(course, "Б"))
    held = frozenset(
        lesson.code for lesson in lessons if lesson.starts_ts <= now
    )
    empty = StudentState(None, {}, held, {}, {})
    lectures = list(student_lessons(course, None))
    present, absent, excused = count_attendance(
        lectures,
        empty.attendance,
        held_codes=held,
        missing_as_absent=True,
    )
    report = build_report(
        StudentState(None, {}, held, {}, {}),
        course,
        now,
    )
    assert (present, absent, excused) == (
        report.attendance_present,
        report.attendance_absent,
        report.attendance_excused,
    )
    partial_marks = {next(iter(held)): "present"} if held else {}
    partial = StudentState("Б", partial_marks, held, {}, {})
    lessons_b = list(student_lessons(course, "Б"))
    present2, absent2, excused2 = count_attendance(
        lessons_b,
        partial.attendance,
        held_codes=held,
        missing_as_absent=True,
    )
    report2 = build_report(partial, course, now)
    assert (present2, absent2, excused2) == (
        report2.attendance_present,
        report2.attendance_absent,
        report2.attendance_excused,
    )


def test_attendance_list_has_three_states() -> None:
    course = load_course(DEFAULT_COURSE_PATH)
    lessons = list(student_lessons(course, "Б"))[:4]
    held = frozenset({lessons[0].code, lessons[1].code, lessons[2].code})
    marks = {lessons[0].code: "present", lessons[1].code: "absent"}
    text = format_attendance_full_list(lessons, marks, held)
    assert "был" in text
    assert "не был" in text
    assert "ещё впереди" in text
    assert "перекличк" not in text
