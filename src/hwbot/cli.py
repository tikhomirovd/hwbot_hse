from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from aiogram import Bot

from hwbot.bot import run_bot
from hwbot.config import load_settings
from hwbot.db import Database
from hwbot.errors import HomeworkNotFoundError
from hwbot.export import format_status_text, status_csv
from hwbot.groups import parse_groups
from hwbot.notify import broadcast_homework
from hwbot.roster import load_roster
from hwbot.timeutil import parse_deadline


async def _with_db(db_path: Path) -> Database:
    db = Database(db_path)
    await db.connect()
    return db


async def cmd_run() -> int:
    await run_bot()
    return 0


async def cmd_import_roster() -> int:
    settings = load_settings()
    db = await _with_db(settings.db_path)
    try:
        rows = load_roster(settings.roster_path)
        await db.seed_roster(rows)
        print(f"Импортировано студентов: {len(rows)}")
        return 0
    finally:
        await db.close()


async def cmd_create_hw(
    title: str,
    text: str,
    deadline: str,
    groups: str,
    broadcast: bool,
) -> int:
    settings = load_settings()
    db = await _with_db(settings.db_path)
    try:
        if await db.student_count() == 0:
            await db.seed_roster(load_roster(settings.roster_path))
        homework = await db.create_homework(
            title=title,
            body=text,
            deadline_ts=parse_deadline(deadline, settings.timezone),
            group_codes=parse_groups(groups),
        )
        sent = 0
        if broadcast:
            bot = Bot(settings.bot_token)
            try:
                sent = await broadcast_homework(bot, db, homework)
            finally:
                await bot.session.close()
        print(f"Создано ДЗ #{homework.id} «{homework.title}», рассылка: {sent}")
        return 0
    finally:
        await db.close()


async def cmd_list_hw() -> int:
    settings = load_settings()
    db = await _with_db(settings.db_path)
    try:
        homeworks = await db.list_homeworks()
        if not homeworks:
            print("ДЗ нет")
            return 0
        for hw in homeworks:
            print(f"#{hw.id}\t{hw.code}\t{hw.title}\t{hw.deadline_ts}")
        return 0
    finally:
        await db.close()


async def cmd_status(homework_id: int) -> int:
    settings = load_settings()
    db = await _with_db(settings.db_path)
    try:
        homework = await db.get_homework(homework_id)
        if homework is None:
            raise HomeworkNotFoundError("Задание не найдено")
        rows = await db.homework_status(homework_id)
        print(format_status_text(homework, rows))
        return 0
    except HomeworkNotFoundError:
        print("Такого ДЗ нет", file=sys.stderr)
        return 1
    finally:
        await db.close()


async def cmd_export(homework_id: int, output: Path | None) -> int:
    settings = load_settings()
    db = await _with_db(settings.db_path)
    try:
        homework = await db.get_homework(homework_id)
        if homework is None:
            raise HomeworkNotFoundError("Задание не найдено")
        rows = await db.homework_status(homework_id)
        csv_text = status_csv(homework, rows)
        if output is None:
            sys.stdout.write(csv_text)
        else:
            output.write_text(csv_text, encoding="utf-8")
            print(f"Записал {output}")
        return 0
    except HomeworkNotFoundError:
        print("Такого ДЗ нет", file=sys.stderr)
        return 1
    finally:
        await db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hwbot", description="Бот сбора ДЗ БАЦРФ")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="Запустить Telegram-бота")
    sub.add_parser("import-roster", help="Залить список студентов из CSV")
    sub.add_parser("list-hw", help="Список ДЗ")

    create = sub.add_parser("create-hw", help="Создать ДЗ и разослать")
    create.add_argument("--title", required=True)
    create.add_argument("--text", required=True)
    create.add_argument("--deadline", required=True)
    create.add_argument("--groups", required=True)
    create.add_argument("--no-broadcast", action="store_true")

    status = sub.add_parser("status", help="Кто сдал")
    status.add_argument("--hw", type=int, required=True)

    export = sub.add_parser("export", help="Выгрузить CSV")
    export.add_argument("--hw", type=int, required=True)
    export.add_argument("--out", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return asyncio.run(cmd_run())
    if args.command == "import-roster":
        return asyncio.run(cmd_import_roster())
    if args.command == "create-hw":
        return asyncio.run(
            cmd_create_hw(
                title=args.title,
                text=args.text,
                deadline=args.deadline,
                groups=args.groups,
                broadcast=not args.no_broadcast,
            )
        )
    if args.command == "list-hw":
        return asyncio.run(cmd_list_hw())
    if args.command == "status":
        return asyncio.run(cmd_status(args.hw))
    if args.command == "export":
        return asyncio.run(cmd_export(args.hw, args.out))
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
