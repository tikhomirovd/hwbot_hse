from __future__ import annotations

from pathlib import Path

import pytest

from hwbot.cli import _read_db_credentials
from hwbot.db import Database
from hwbot.formatting import db_access_missing_text, db_access_text
from hwbot.models import DbCredential, Student
from hwbot.roster import load_roster


@pytest.fixture
async def seeded(db: Database, roster_path: Path) -> Database:
    await db.seed_roster(load_roster(roster_path))
    return db


def _write_csv(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------- чтение


def test_read_credentials(tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path / "pw.csv",
        "login,password\nivanov,abc123\npetrova,def456\n",
    )
    assert _read_db_credentials(path) == [
        DbCredential("ivanov", "abc123"),
        DbCredential("petrova", "def456"),
    ]


def test_read_credentials_rejects_wrong_header(tmp_path: Path) -> None:
    path = _write_csv(tmp_path / "pw.csv", "user,secret\nivanov,abc\n")
    with pytest.raises(ValueError, match="login"):
        _read_db_credentials(path)


def test_read_credentials_rejects_half_empty_row(tmp_path: Path) -> None:
    path = _write_csv(tmp_path / "pw.csv", "login,password\nivanov,\n")
    with pytest.raises(ValueError, match="Строка 2"):
        _read_db_credentials(path)


# --------------------------------------------------------------------- запись


async def test_set_credentials_matches_by_email_local_part(seeded: Database) -> None:
    updated, orphans = await seeded.set_db_credentials(
        [DbCredential("ivanov", "abc123"), DbCredential("petrova", "def456")]
    )
    assert (updated, orphans) == (2, [])
    students = {item.full_name: item for item in await seeded.list_students()}
    ivanov = students["Иванов Иван Иванович"]
    assert ivanov.db_login == "ivanov"
    assert ivanov.db_password == "abc123"


async def test_set_credentials_reports_orphans(seeded: Database) -> None:
    updated, orphans = await seeded.set_db_credentials(
        [DbCredential("ivanov", "abc"), DbCredential("nobody_1", "xyz")]
    )
    assert updated == 1
    assert orphans == ["nobody_1"]


async def test_set_credentials_is_idempotent(seeded: Database) -> None:
    await seeded.set_db_credentials([DbCredential("ivanov", "old")])
    await seeded.set_db_credentials([DbCredential("ivanov", "new")])
    students = {item.full_name: item for item in await seeded.list_students()}
    assert students["Иванов Иван Иванович"].db_password == "new"


async def test_students_without_credentials_stay_empty(seeded: Database) -> None:
    students = await seeded.list_students()
    assert all(item.db_login is None for item in students)
    assert all(item.db_password is None for item in students)


# ------------------------------------------------------------------ сообщение


def _student(db_login: str | None, db_password: str | None) -> Student:
    return Student(
        id=1,
        full_name="Иванов Иван Иванович",
        group_code="БАЦРФ261",
        email="ivanov@example.edu",
        telegram_id=111,
        telegram_username="ivanov",
        db_login=db_login,
        db_password=db_password,
    )


def test_access_text_contains_both_ports() -> None:
    text = db_access_text(_student("ivanov", "s3cret"))
    assert "ivanov:s3cret@2.56.240.205:5432/prime?sslmode=require" in text
    assert "ivanov:s3cret@2.56.240.205:443/prime?sslmode=require" in text
    assert "PRIME_DSN=" in text


def test_access_text_warns_against_forwarding() -> None:
    text = db_access_text(_student("ivanov", "s3cret"))
    assert "Не пересылай" in text


def test_missing_text_does_not_blame_the_student() -> None:
    text = db_access_missing_text()
    assert "преподавателю" in text
    assert "s3cret" not in text
