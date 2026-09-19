from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message

from hwbot.config import Settings, is_admin
from hwbot.db import Database
from hwbot.errors import HomeworkNotFoundError
from hwbot.export import format_status_text, status_csv
from hwbot.course import DEFAULT_COURSE_PATH, load_course
from hwbot.formatting import (
    format_course_overview,
    format_gradebook_board,
    format_status_board,
    format_status_report,
    format_students_report,
)
from hwbot.models import Assessment
from hwbot.ops import reports_for_students
from hwbot.overview import build_course_overview
from hwbot.telegramutil import answer_long, escape_html
from hwbot.timeutil import now_ts

router = Router()


async def _admin_ok(message: Message, settings: Settings) -> bool:
    if message.from_user is not None and is_admin(message.from_user.id, settings):
        return True
    await message.answer("Это команда преподавателя.")
    return False


async def resolve_homework_ref(db: Database, token: str) -> Assessment | None:
    raw = token.strip()
    if not raw:
        return None
    try:
        homework_id = int(raw)
    except ValueError:
        return await db.get_assessment_by_code(raw.casefold())
    return await db.get_homework(homework_id)


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
        lines = ["Какое ДЗ? Напиши номер или код, например /status 1 или /status hw1"]
        for hw in homeworks:
            lines.append(f"/{command.command} {hw.id} — {hw.title}")
        await message.answer("\n".join(lines))
        return None
    homework = await resolve_homework_ref(db, args.split()[0])
    if homework is None:
        token = args.split()[0]
        try:
            int(token)
        except ValueError:
            await message.answer(
                "Нужен номер или код ДЗ, например /status 1 или /status hw1"
            )
            return None
        await message.answer("Такого ДЗ нет.")
        return None
    return homework.id


@router.message(Command("overview"))
async def cmd_overview(
    message: Message,
    db: Database,
    settings: Settings,
) -> None:
    if not await _admin_ok(message, settings):
        return
    overview = await build_course_overview(db, now_ts())
    await answer_long(message, format_course_overview(overview))


@router.message(Command("gradebook"))
async def cmd_gradebook(
    message: Message,
    db: Database,
    settings: Settings,
) -> None:
    if not await _admin_ok(message, settings):
        return
    rows = await reports_for_students(db, load_course(DEFAULT_COURSE_PATH), now_ts())
    board = escape_html("\n".join(format_gradebook_board(rows)))
    await answer_long(
        message,
        "Ведомость: «идёшь» — по проверенному, «карман» — накопленная сейчас "
        f"(из 7), «ноль» — если больше ничего не сдавать.\n\n<pre>{board}</pre>",
    )


@router.message(Command("students"))
async def cmd_students(
    message: Message,
    command: CommandObject,
    db: Database,
    settings: Settings,
) -> None:
    if not await _admin_ok(message, settings):
        return
    raw = (command.args or "").strip().casefold()
    if raw and raw not in {"missing", "registered"}:
        await message.answer("Напиши /students или /students missing")
        return
    students = await db.list_students()
    await message.answer(format_students_report(students, registered=raw != "missing"))


@router.message(Command("status"))
async def cmd_status(
    message: Message,
    command: CommandObject,
    db: Database,
    settings: Settings,
) -> None:
    if not await _admin_ok(message, settings):
        return
    args = (command.args or "").strip()
    if not args:
        homeworks = await db.list_homeworks(active_only=True)
        if not homeworks:
            await message.answer("ДЗ ещё нет.")
            return
        students = await db.list_students()
        latest = await db.latest_submissions_map()
        await answer_long(
            message, format_status_board(homeworks, students, latest, html=True)
        )
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
    await answer_long(message, format_status_report(homework, rows, html=True))


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
