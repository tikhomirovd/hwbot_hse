from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from hwbot.db import Database
from hwbot.models import Student
from hwbot.roster import load_roster

FIXTURE_ROSTER = Path(__file__).resolve().parent / "fixtures" / "roster.csv"


@pytest.fixture
def roster_path() -> Path:
    return FIXTURE_ROSTER


@pytest.fixture
def roster_students(roster_path: Path) -> list[Student]:
    rows = load_roster(roster_path)
    return [
        Student(
            id=index,
            full_name=row.full_name,
            group_code=row.group_code,
            email=row.email,
            telegram_id=None,
            telegram_username=None,
        )
        for index, row in enumerate(rows, start=1)
    ]


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "test.db")
    await database.connect()
    yield database
    await database.close()
