from __future__ import annotations

from hwbot.cli import build_parser


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
