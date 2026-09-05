from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message

from hwbot.config import Settings, is_admin
from hwbot.db import Database
from hwbot.errors import HomeworkNotFoundError
from hwbot.export import format_status_text, status_csv

router = Router()


async def _admin_ok(message: Message, settings: Settings) -> bool:
    if message.from_user is not None and is_admin(message.from_user.id, settings):
        return True
    await message.answer("Это команда преподавателя.")
    return False


async def _homework_from_command(
    message: Message,
    command: CommandObject,
    db: Database,
) -> int | None:
    args = (command.args or "").strip()
    if not args:
        homeworks = await db.list_homeworks(active_only=True)
        if not homeworks:
            await message.answer("ДЗ ещё нет.")
            return None
        lines = ["Какое ДЗ? Напиши номер, например /status 1"]
        for hw in homeworks:
            lines.append(f"/{command.command} {hw.id} — {hw.title}")
        await message.answer("\n".join(lines))
        return None
    try:
        return int(args.split()[0])
    except ValueError:
        await message.answer("Нужен номер ДЗ, например /status 1")
        return None


@router.message(Command("status"))
async def cmd_status(
    message: Message,
    command: CommandObject,
    db: Database,
    settings: Settings,
) -> None:
    if not await _admin_ok(message, settings):
        return
    homework_id = await _homework_from_command(message, command, db)
    if homework_id is None:
        return
    try:
        homework = await db.get_homework(homework_id)
        if homework is None:
            raise HomeworkNotFoundError("Задание не найдено")
        rows = await db.homework_status(homework_id)
    except HomeworkNotFoundError:
        await message.answer("Такого ДЗ нет.")
        return
    await message.answer(format_status_text(homework, rows))


@router.message(Command("missing"))
async def cmd_missing(
    message: Message,
    command: CommandObject,
    db: Database,
    settings: Settings,
) -> None:
    if not await _admin_ok(message, settings):
        return
    homework_id = await _homework_from_command(message, command, db)
    if homework_id is None:
        return
    try:
        homework = await db.get_homework(homework_id)
        if homework is None:
            raise HomeworkNotFoundError("Задание не найдено")
        rows = await db.homework_status(homework_id)
    except HomeworkNotFoundError:
        await message.answer("Такого ДЗ нет.")
        return
    missing = [row for row in rows if row.submission is None]
    if not missing:
        await message.answer(f"По «{homework.title}» все сдали.")
        return
    lines = [f"Не сдали «{homework.title}» ({len(missing)}):"]
    for row in missing:
        lines.append(f"— {row.student.full_name} ({row.student.group_code})")
    await message.answer("\n".join(lines))


@router.message(Command("export"))
async def cmd_export(
    message: Message,
    command: CommandObject,
    db: Database,
    settings: Settings,
) -> None:
    if not await _admin_ok(message, settings):
        return
    homework_id = await _homework_from_command(message, command, db)
    if homework_id is None:
        return
    try:
        homework = await db.get_homework(homework_id)
        if homework is None:
            raise HomeworkNotFoundError("Задание не найдено")
        rows = await db.homework_status(homework_id)
    except HomeworkNotFoundError:
        await message.answer("Такого ДЗ нет.")
        return
    content = status_csv(homework, rows).encode("utf-8")
    document = BufferedInputFile(content, filename=f"hw-{homework.id}.csv")
    await message.answer_document(document, caption=format_status_text(homework, rows))
