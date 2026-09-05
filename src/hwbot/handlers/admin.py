from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, Message

from hwbot.config import Settings, is_admin
from hwbot.db import Database
from hwbot.errors import HomeworkNotFoundError
from hwbot.export import format_status_text, status_csv
from hwbot.groups import UnknownGroupError, parse_groups
from hwbot.notify import broadcast_homework
from hwbot.timeutil import parse_deadline

router = Router()


class NewHwStates(StatesGroup):
    title = State()
    body = State()
    deadline = State()
    groups = State()


def _admin_ok(message: Message, settings: Settings) -> bool:
    return message.from_user is not None and is_admin(message.from_user.id, settings)


@router.message(Command("newhw"))
async def cmd_newhw(message: Message, state: FSMContext, settings: Settings) -> None:
    if not _admin_ok(message, settings):
        return
    await state.set_state(NewHwStates.title)
    await message.answer("Название ДЗ? Отмена: /cancel")


@router.message(StateFilter(NewHwStates.title), F.text)
async def newhw_title(message: Message, state: FSMContext) -> None:
    if message.text is None:
        return
    title = message.text.strip()
    if not title:
        await message.answer("Название не должно быть пустым.")
        return
    await state.update_data(title=title)
    await state.set_state(NewHwStates.body)
    await message.answer("Текст задания:")


@router.message(StateFilter(NewHwStates.body), F.text)
async def newhw_body(message: Message, state: FSMContext) -> None:
    if message.text is None:
        return
    body = message.text.strip()
    if not body:
        await message.answer("Текст не должен быть пустым.")
        return
    await state.update_data(body=body)
    await state.set_state(NewHwStates.deadline)
    await message.answer("Дедлайн в Москве, например 2026-09-12 23:59")


@router.message(StateFilter(NewHwStates.deadline), F.text)
async def newhw_deadline(message: Message, state: FSMContext) -> None:
    if message.text is None:
        return
    try:
        deadline_ts = parse_deadline(message.text)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.update_data(deadline_ts=deadline_ts)
    await state.set_state(NewHwStates.groups)
    await message.answer("Группы: 261, 262 или обе 261,262")


@router.message(StateFilter(NewHwStates.groups), F.text)
async def newhw_groups(
    message: Message,
    state: FSMContext,
    db: Database,
) -> None:
    if message.text is None or message.bot is None:
        return
    try:
        groups = parse_groups(message.text)
    except UnknownGroupError as exc:
        await message.answer(str(exc))
        return
    data = await state.get_data()
    title = data.get("title")
    body = data.get("body")
    deadline_ts = data.get("deadline_ts")
    if not isinstance(title, str) or not isinstance(body, str) or not isinstance(deadline_ts, int):
        await state.clear()
        await message.answer("Сломался диалог, начни /newhw заново.")
        return
    homework = await db.create_homework(title, body, deadline_ts, groups)
    sent = await broadcast_homework(message.bot, db, homework)
    await state.clear()
    await message.answer(
        f"Создал ДЗ #{homework.id} «{homework.title}».\n"
        f"Разослал {sent} студентам."
    )


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
    if not _admin_ok(message, settings):
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
    if not _admin_ok(message, settings):
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
    if not _admin_ok(message, settings):
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
