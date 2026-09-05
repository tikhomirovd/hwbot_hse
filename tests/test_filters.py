from __future__ import annotations

from hwbot.handlers.filters import is_plain_text


def test_plain_text_rejects_commands() -> None:
    assert is_plain_text("/cancel") is False
    assert is_plain_text("/start") is False
    assert is_plain_text("  /help") is False
    assert is_plain_text("/cancel@hse_python_2627_bot") is False


def test_plain_text_accepts_homework_input() -> None:
    assert is_plain_text("ДЗ 1. Git") is True
    assert is_plain_text("2026-09-12 23:59") is True
    assert is_plain_text("261,262") is True
    assert is_plain_text(None) is False
    assert is_plain_text("") is False
