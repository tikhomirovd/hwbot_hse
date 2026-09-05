from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from hwbot.config import MOSCOW_TZ

DEADLINE_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M")


def zone(name: str = MOSCOW_TZ) -> ZoneInfo:
    return ZoneInfo(name)


def now_ts(tz_name: str = MOSCOW_TZ) -> int:
    return int(datetime.now(zone(tz_name)).timestamp())


def parse_local_date_time(date_text: str, time_text: str, tz_name: str = MOSCOW_TZ) -> int:
    return parse_deadline(f"{date_text.strip()} {time_text.strip()}", tz_name)


def parse_deadline(raw: str, tz_name: str = MOSCOW_TZ) -> int:
    text = raw.strip()
    last_error: ValueError | None = None
    for fmt in DEADLINE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError as exc:
            last_error = exc
            continue
        localized = parsed.replace(tzinfo=zone(tz_name))
        return int(localized.timestamp())
    raise ValueError(
        "Не понял дедлайн. Формат: 2026-09-12 23:59"
    ) from last_error


def format_dt(ts: int, tz_name: str = MOSCOW_TZ) -> str:
    return datetime.fromtimestamp(ts, zone(tz_name)).strftime("%d.%m.%Y %H:%M")


def is_deadline_open(deadline_ts: int, now: int | None = None) -> bool:
    current = now_ts() if now is None else now
    return current <= deadline_ts


def remaining_seconds(deadline_ts: int, now: int | None = None) -> int:
    current = now_ts() if now is None else now
    return deadline_ts - current


def format_remaining(deadline_ts: int, now: int | None = None) -> str:
    left = remaining_seconds(deadline_ts, now)
    if left <= 0:
        return "дедлайн прошёл"
    days, rem = divmod(left, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    if minutes and not days:
        parts.append(f"{minutes} мин")
    if not parts:
        parts.append("меньше минуты")
    return "осталось " + " ".join(parts)
