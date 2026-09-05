from __future__ import annotations

from hwbot.course import DEFAULT_COURSE_PATH, load_course
from hwbot.export import format_status_text, gradebook_csv, status_csv
from hwbot.grading import StudentState, build_report
from hwbot.models import Assessment, HomeworkStatusRow, Student, Submission
from hwbot.timeutil import parse_deadline


def test_status_csv_and_text() -> None:
    homework = Assessment(
        id=3,
        code="hw1",
        label="ДЗ-1",
        title="ДЗ 1",
        body="body",
        component="homework",
        weight_final=0.0625,
        submit_via_bot=True,
        issued_at=1,
        deadline_ts=1,
        accept_until_ts=1 + 7 * 86400,
        graded_on_ts=None,
        late_rule="homework",
        blocking=False,
        active=True,
    )
    done = Student(1, "Абрамова Анастасия Романовна", "БАЦРФ261", "a@edu.hse.ru", 1, "a")
    missing = Student(2, "Губарев Ярослав Игоревич", "БАЦРФ261", "b@edu.hse.ru", None, None)
    rows = [
        HomeworkStatusRow(
            student=done,
            submission=Submission(1, 1, 3, "https://github.com/x", 1_700_000_000),
            accept_until_ts=homework.accept_until_ts,
            now_ts=2,
        ),
        HomeworkStatusRow(
            student=missing,
            submission=None,
            accept_until_ts=homework.accept_until_ts,
            now_ts=2,
        ),
    ]
    csv_text = status_csv(homework, rows)
    assert "сдано" in csv_text
    assert "не сдано" in csv_text
    assert "https://github.com/x" in csv_text
    text = format_status_text(homework, rows)
    assert "Сдали: 1 / 2" in text
    assert "Губарев" in text


def test_gradebook_empty_rows() -> None:
    course = load_course(DEFAULT_COURSE_PATH)
    student = Student(1, "Тест Тестов", "БАЦРФ261", "t@edu.hse.ru", None, None, "Б")
    report = build_report(
        StudentState("Б", {}, frozenset(), {}, {}),
        course,
        parse_deadline("2026-09-06 12:00"),
    )
    text = gradebook_csv(course, [(student, report)])
    assert "ФИО" in text
    assert "ДЗ-1" in text
    assert "Идёшь на" in text
    assert "Тест Тестов" in text
