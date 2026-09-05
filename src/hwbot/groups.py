from __future__ import annotations

from hwbot.config import GROUP_PREFIX, KNOWN_GROUPS


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
