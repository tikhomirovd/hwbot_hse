from __future__ import annotations

from hwbot.cli import build_parser, format_students_listing
from hwbot.models import Student


def test_seed_and_grade_parsers() -> None:
    parser = build_parser()
    seed = parser.parse_args(["seed-course", "--dry-run"])
    assert seed.command == "seed-course"
    assert seed.dry_run
    mark = parser.parse_args(
        ["attendance", "mark", "--lesson", "L03", "--present", "Иванов"]
    )
    assert mark.att_command == "mark"
    assert mark.lesson == "L03"
    grade = parser.parse_args(
        ["grade", "set", "--assessment", "hw1", "--student", "Иванов", "--score", "8.5"]
    )
    assert grade.grade_command == "set"
    assert grade.score == 8.5
    book = parser.parse_args(["gradebook", "--out", "/tmp/g.csv"])
    assert book.command == "gradebook"


def test_broadcast_and_students_parsers() -> None:
    broadcast = build_parser().parse_args(["broadcast", "--text", "Сервер лежит"])
    assert broadcast.command == "broadcast"
    assert broadcast.text == "Сервер лежит"
    registered = build_parser().parse_args(["students", "--registered"])
    assert registered.registered
    missing = build_parser().parse_args(["students", "--missing"])
    assert missing.missing
    unbind = build_parser().parse_args(["unbind", "--student", "Иванов"])
    assert unbind.student == "Иванов"


def test_students_listing_counts() -> None:
    bound = Student(1, "Абрамова Анастасия Романовна", "БАЦРФ261", "a@edu.hse.ru", 1, "a")
    free = Student(2, "Губарев Ярослав Игоревич", "БАЦРФ261", "b@edu.hse.ru", None, None)
    text = format_students_listing([bound, free], registered=None)
    assert "зарегистрировано 1 из 2" in text
    assert "не зарегистрированы" in text
    assert "Губарев" in text
    assert "Абрамова" not in text
    missing = format_students_listing([bound, free], registered=False)
    assert "не зарегистрированы: 1 из 2" in missing
    registered = format_students_listing([bound, free], registered=True)
    assert "Абрамова" in registered
    assert "Губарев" not in registered
