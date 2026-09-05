from __future__ import annotations

from hwbot.matching import match_students, normalize_text
from hwbot.models import Student


def test_normalize_yo() -> None:
    assert normalize_text("Семёнов Фёдор Николаевич") == "семенов федор николаевич"


def test_email_match(roster_students: list[Student]) -> None:
    result = match_students("semenov@example.edu", roster_students)
    assert result.unique is not None
    assert result.unique.full_name == "Семёнов Фёдор Николаевич"


def test_full_name_with_yo_variant(roster_students: list[Student]) -> None:
    result = match_students("Семенов Федор Николаевич", roster_students)
    assert result.unique is not None
    assert result.unique.full_name == "Семёнов Фёдор Николаевич"


def test_last_and_first_unique(roster_students: list[Student]) -> None:
    result = match_students("Иванов Иван", roster_students)
    assert result.unique is not None
    assert result.unique.email == "ivanov@example.edu"


def test_unknown_name(roster_students: list[Student]) -> None:
    result = match_students("Пушкин Александр Сергеевич", roster_students)
    assert result.students == ()


def test_empty_query(roster_students: list[Student]) -> None:
    result = match_students("   ", roster_students)
    assert result.students == ()
