from __future__ import annotations

from pathlib import Path

import pytest

from hwbot.roster import RosterError, load_roster


def test_load_example_roster(roster_path: Path) -> None:
    rows = load_roster(roster_path)
    assert len(rows) == 4
    group_261 = [row for row in rows if row.group_code == "БАЦРФ261"]
    group_262 = [row for row in rows if row.group_code == "БАЦРФ262"]
    assert len(group_261) == 2
    assert len(group_262) == 2
    assert all(row.seminar_group == "261" for row in group_261)
    assert all(row.seminar_group == "262" for row in group_262)
    assert all("@" in row.email for row in rows)
    names = {row.full_name for row in rows}
    assert "Семёнов Фёдор Николаевич" in names


def test_roster_rejects_birth_date(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text(
        "full_name,group_code,email,dob\nИванов Иван,БАЦРФ261,a@edu.hse.ru,01.01.2000\n",
        encoding="utf-8",
    )
    with pytest.raises(RosterError, match="дату рождения"):
        load_roster(path)


def test_optional_seminar_group(tmp_path: Path) -> None:
    path = tmp_path / "roster.csv"
    path.write_text(
        "full_name,group_code,email,seminar_group\n"
        "Иванов Иван Иванович,БАЦРФ261,a@edu.hse.ru,А\n"
        "Петрова Анна Сергеевна,БАЦРФ262,b@edu.hse.ru,\n",
        encoding="utf-8",
    )
    rows = load_roster(path)
    assert rows[0].seminar_group == "261"
    assert rows[1].seminar_group == "262"


def test_example_roster_has_no_birth_column(roster_path: Path) -> None:
    text = roster_path.read_text(encoding="utf-8")
    assert "рождения" not in text.casefold()
    assert "16.01.2005" not in text
