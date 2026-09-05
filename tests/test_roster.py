from __future__ import annotations

from pathlib import Path

import pytest

from hwbot.roster import RosterError, load_roster


def test_load_real_roster(roster_path: Path) -> None:
    rows = load_roster(roster_path)
    assert len(rows) == 57
    group_261 = [row for row in rows if row.group_code == "БАЦРФ261"]
    group_262 = [row for row in rows if row.group_code == "БАЦРФ262"]
    assert len(group_261) == 28
    assert len(group_262) == 29
    assert all("@" in row.email for row in rows)
    names = {row.full_name for row in rows}
    assert "Михайлов Фёдор Николаевич" in names


def test_roster_rejects_birth_date(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text(
        "full_name,group_code,email,dob\nИванов Иван,БАЦРФ261,a@edu.hse.ru,01.01.2000\n",
        encoding="utf-8",
    )
    with pytest.raises(RosterError, match="дату рождения"):
        load_roster(path)


def test_real_roster_has_no_birth_column(roster_path: Path) -> None:
    text = roster_path.read_text(encoding="utf-8")
    assert "рождения" not in text.casefold()
    assert "16.01.2005" not in text
