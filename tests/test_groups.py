from __future__ import annotations

import pytest

from hwbot.groups import (
    UnknownGroupError,
    canonical_seminar_group,
    parse_groups,
    seminar_from_group_code,
    short_group,
)


def test_parse_short_groups() -> None:
    assert parse_groups("261,262") == ("БАЦРФ261", "БАЦРФ262")


def test_parse_full_names() -> None:
    assert parse_groups("БАЦРФ261") == ("БАЦРФ261",)


def test_unknown_group() -> None:
    with pytest.raises(UnknownGroupError):
        parse_groups("999")


def test_short_label() -> None:
    assert short_group("БАЦРФ261") == "261"


def test_seminar_from_academic_group() -> None:
    assert seminar_from_group_code("БАЦРФ261") == "261"
    assert seminar_from_group_code("БАЦРФ262") == "262"
    assert canonical_seminar_group("А") == "261"
    assert canonical_seminar_group("Б") == "262"
    assert canonical_seminar_group("261") == "261"
