from __future__ import annotations

import csv
from pathlib import Path

from hwbot.groups import canonical_group
from hwbot.models import RosterRow

FORBIDDEN_FIELDS = {"дата рождения", "date_of_birth", "dob", "birth_date"}


class RosterError(ValueError):
    pass


def load_roster(path: Path) -> list[RosterRow]:
    if not path.exists():
        raise RosterError(f"Нет файла списка: {path}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise RosterError("Пустой CSV: нет заголовков")
        headers = [name.strip() for name in reader.fieldnames]
        lowered = {name.casefold() for name in headers}
        if lowered & FORBIDDEN_FIELDS:
            raise RosterError("В ростере нельзя хранить дату рождения")
        required = {"full_name", "group_code", "email"}
        if not required.issubset({name.strip() for name in headers}):
            raise RosterError("Нужны колонки full_name, group_code, email")
        rows: list[RosterRow] = []
        seen_names: set[str] = set()
        seen_emails: set[str] = set()
        for index, raw in enumerate(reader, start=2):
            full_name = (raw.get("full_name") or "").strip()
            group_code = (raw.get("group_code") or "").strip()
            email = (raw.get("email") or "").strip().casefold()
            if not full_name or not group_code or not email:
                raise RosterError(f"Пустая строка в ростере: строка {index}")
            if full_name in seen_names:
                raise RosterError(f"Дубль ФИО: {full_name}")
            if email in seen_emails:
                raise RosterError(f"Дубль почты: {email}")
            seen_names.add(full_name)
            seen_emails.add(email)
            rows.append(
                RosterRow(
                    full_name=full_name,
                    group_code=canonical_group(group_code),
                    email=email,
                )
            )
    if not rows:
        raise RosterError("Ростер пустой")
    return rows
