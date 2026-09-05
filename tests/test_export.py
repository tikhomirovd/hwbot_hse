from __future__ import annotations

from hwbot.export import format_status_text, status_csv
from hwbot.models import Assessment, HomeworkStatusRow, Student, Submission


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
