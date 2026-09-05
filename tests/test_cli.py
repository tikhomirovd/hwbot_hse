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


def test_create_hw_parser() -> None:
    args = build_parser().parse_args(
        [
            "create-hw",
            "--title",
            "ДЗ 1",
            "--text",
            "Ссылка",
            "--deadline",
            "2026-09-12 23:59",
            "--groups",
            "261,262",
            "--no-broadcast",
        ]
    )
    assert args.command == "create-hw"
    assert args.no_broadcast
    assert args.groups == "261,262"
