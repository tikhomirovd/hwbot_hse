from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from hwbot.config import MOSCOW_TZ

DEADLINE_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M")
MONTHS_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
WEEKDAYS_PREPOSITIONAL = (
    "понедельникам",
    "вторникам",
    "средам",
    "четвергам",
    "пятницам",
    "субботам",
    "воскресеньям",
)
QUIET_START_HOUR = 23
QUIET_END_HOUR = 10


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


def format_human_dt(ts: int, tz_name: str = MOSCOW_TZ) -> str:
    moment = datetime.fromtimestamp(ts, zone(tz_name))
    return (
        f"{moment.day} {MONTHS_GENITIVE[moment.month - 1]}, "
        f"{moment.strftime('%H:%M')}"
    )


def format_human_day(ts: int, tz_name: str = MOSCOW_TZ) -> str:
    moment = datetime.fromtimestamp(ts, zone(tz_name))
    return f"{moment.day} {MONTHS_GENITIVE[moment.month - 1]}"


def format_human_clock(ts: int, tz_name: str = MOSCOW_TZ) -> str:
    return datetime.fromtimestamp(ts, zone(tz_name)).strftime("%H:%M")


def format_human_datetime(ts: int, tz_name: str = MOSCOW_TZ) -> str:
    moment = datetime.fromtimestamp(ts, zone(tz_name))
    return (
        f"{moment.day} {MONTHS_GENITIVE[moment.month - 1]} "
        f"в {moment.strftime('%H:%M')}"
    )


def is_same_calendar_day(left: int, right: int, tz_name: str = MOSCOW_TZ) -> bool:
    first = datetime.fromtimestamp(left, zone(tz_name)).date()
    second = datetime.fromtimestamp(right, zone(tz_name)).date()
    return first == second


def weekday_prepositional(ts: int, tz_name: str = MOSCOW_TZ) -> str:
    moment = datetime.fromtimestamp(ts, zone(tz_name))
    return WEEKDAYS_PREPOSITIONAL[moment.weekday()]


def is_quiet_hours(now: int, tz_name: str = MOSCOW_TZ) -> bool:
    hour = datetime.fromtimestamp(now, zone(tz_name)).hour
    return hour >= QUIET_START_HOUR or hour < QUIET_END_HOUR


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
