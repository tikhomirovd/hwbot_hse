from __future__ import annotations

from hwbot.config import GROUP_PREFIX, KNOWN_GROUPS

SEMINAR_GROUPS = ("261", "262")


class UnknownGroupError(ValueError):
    pass


def canonical_group(raw: str) -> str:
    value = raw.strip().upper().replace(" ", "")
    if value in KNOWN_GROUPS:
        return value
    if value.isdigit():
        candidate = f"{GROUP_PREFIX}{value}"
        if candidate in KNOWN_GROUPS:
            return candidate
    if value.startswith(GROUP_PREFIX) and value[len(GROUP_PREFIX) :].isdigit():
        if value in KNOWN_GROUPS:
            return value
    raise UnknownGroupError(f"Неизвестная группа: {raw}")


def parse_groups(raw: str) -> tuple[str, ...]:
    parts = [item.strip() for item in raw.replace(";", ",").split(",") if item.strip()]
    if not parts:
        raise UnknownGroupError("Нужно указать хотя бы одну группу")
    groups = tuple(dict.fromkeys(canonical_group(part) for part in parts))
    return groups


def short_group(group_code: str) -> str:
    if group_code.startswith(GROUP_PREFIX):
        return group_code[len(GROUP_PREFIX) :]
    return group_code


def seminar_from_group_code(group_code: str) -> str:
    short = short_group(group_code)
    if short in SEMINAR_GROUPS:
        return short
    raise UnknownGroupError(f"Неизвестная семинарская группа для {group_code}")


def canonical_seminar_group(raw: str) -> str:
    value = raw.strip()
    if value in {"А", "A", "а", "a"}:
        return "261"
    if value in {"Б", "B", "б", "b"}:
        return "262"
    if value in SEMINAR_GROUPS:
        return value
    try:
        return seminar_from_group_code(canonical_group(value))
    except UnknownGroupError as exc:
        raise UnknownGroupError(
            f"Семинарская группа должна быть 261 или 262: {raw}"
        ) from exc
