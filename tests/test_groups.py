from __future__ import annotations

import pytest

from hwbot.groups import UnknownGroupError, parse_groups, short_group


def test_parse_short_groups() -> None:
    assert parse_groups("261,262") == ("БАЦРФ261", "БАЦРФ262")


def test_parse_full_names() -> None:
    assert parse_groups("БАЦРФ261") == ("БАЦРФ261",)


def test_unknown_group() -> None:
    with pytest.raises(UnknownGroupError):
        parse_groups("999")


def test_short_label() -> None:
    assert short_group("БАЦРФ261") == "261"
