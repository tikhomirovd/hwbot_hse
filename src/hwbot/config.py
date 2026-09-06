from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "bot.db"
DEFAULT_ROSTER_PATH = PROJECT_ROOT / "data" / "roster.csv"
MOSCOW_TZ = "Europe/Moscow"
GROUP_PREFIX = "БАЦРФ"
KNOWN_GROUPS = ("БАЦРФ261", "БАЦРФ262")

# Учебный PostgreSQL. Адрес не секрет — секрет только пароль студента,
# он лежит в ведомости и уходит личным сообщением по /db.
# Порт 443 — тот же сервер: из сети ВШЭ и из части корпоративных VPN
# высокие порты закрыты, а 443 не закрывают нигде.
PRIME_DB_HOST = "2.56.240.205"
PRIME_DB_PORT = 5432
PRIME_DB_FALLBACK_PORT = 443
PRIME_DB_NAME = "prime"
PRIME_DB_HELP_URL = (
    "https://github.com/tikhomirovd/python-for-ba-hse-2026"
    "/blob/master/справка/подключение-к-базе.md"
)


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    admin_telegram_ids: frozenset[int]
    db_path: Path
    roster_path: Path
    timezone: str


def _parse_admin_ids(raw: str) -> frozenset[int]:
    ids: set[int] = set()
    for part in raw.split(","):
        item = part.strip()
        if item:
            ids.add(int(item))
    return frozenset(ids)


def load_settings(env_file: Path | None = None) -> Settings:
    load_dotenv(env_file or PROJECT_ROOT / ".env")
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is not set")
    admin_raw = os.environ.get("ADMIN_TELEGRAM_IDS", "").strip()
    if not admin_raw:
        raise RuntimeError("ADMIN_TELEGRAM_IDS is not set")
    db_raw = os.environ.get("DB_PATH", str(DEFAULT_DB_PATH))
    roster_raw = os.environ.get("ROSTER_PATH", str(DEFAULT_ROSTER_PATH))
    db_path = Path(db_raw)
    roster_path = Path(roster_raw)
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    if not roster_path.is_absolute():
        roster_path = PROJECT_ROOT / roster_path
    return Settings(
        bot_token=token,
        admin_telegram_ids=_parse_admin_ids(admin_raw),
        db_path=db_path,
        roster_path=roster_path,
        timezone=os.environ.get("TIMEZONE", MOSCOW_TZ).strip() or MOSCOW_TZ,
    )


def is_admin(telegram_id: int, settings: Settings) -> bool:
    return telegram_id in settings.admin_telegram_ids
