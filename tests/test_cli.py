from __future__ import annotations

from hwbot.cli import build_parser


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
