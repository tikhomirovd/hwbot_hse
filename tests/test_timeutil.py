from __future__ import annotations

from hwbot.timeutil import (
    format_remaining,
    is_deadline_open,
    parse_deadline,
    remaining_seconds,
)


def test_parse_deadline_moscow() -> None:
    ts = parse_deadline("2026-09-12 23:59")
    assert remaining_seconds(ts, ts) == 0
    assert is_deadline_open(ts, ts)
    assert not is_deadline_open(ts, ts + 1)


def test_format_remaining() -> None:
    deadline = 1_000_000
    assert format_remaining(deadline, deadline - 12 * 3600) == "осталось 12 ч"
    assert "дедлайн прошёл" in format_remaining(deadline, deadline + 10)
