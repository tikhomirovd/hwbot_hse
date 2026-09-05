from __future__ import annotations

from hwbot.export import format_status_text, status_csv
from hwbot.models import Homework, HomeworkStatusRow, Student, Submission


def test_status_csv_and_text() -> None:
    homework = Homework(
        id=3,
        title="ДЗ 1",
        body="body",
        deadline_ts=1,
        group_codes=("БАЦРФ261",),
        active=True,
        created_at=1,
    )
    done = Student(1, "Абрамова Анастасия Романовна", "БАЦРФ261", "a@edu.hse.ru", 1, "a")
    missing = Student(2, "Губарев Ярослав Игоревич", "БАЦРФ261", "b@edu.hse.ru", None, None)
    rows = [
        HomeworkStatusRow(
            student=done,
            submission=Submission(1, 1, 3, "https://github.com/x", 1_700_000_000),
        ),
        HomeworkStatusRow(student=missing, submission=None),
    ]
    csv_text = status_csv(homework, rows)
    assert "сдано" in csv_text
    assert "не сдано (0)" in csv_text
    assert "https://github.com/x" in csv_text
    text = format_status_text(homework, rows)
    assert "Сдали: 1 / 2" in text
    assert "Губарев" in text
