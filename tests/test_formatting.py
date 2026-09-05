from __future__ import annotations

from hwbot.formatting import homework_status_for_student
from hwbot.models import Homework, Submission


def _hw(deadline: int) -> Homework:
    return Homework(1, "ДЗ 1", "body", deadline, ("БАЦРФ261",), True, 1)


def test_status_labels() -> None:
    homework = _hw(100)
    assert homework_status_for_student(homework, None, 50) == "не сдано"
    assert homework_status_for_student(homework, None, 101) == "закрыто · 0"
    submitted = Submission(1, 1, 1, "https://github.com/x", 40)
    assert homework_status_for_student(homework, submitted, 200) == "сдано"
