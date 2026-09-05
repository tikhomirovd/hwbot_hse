from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from hwbot.commands import setup_commands
from hwbot.config import Settings, load_settings
from hwbot.db import Database
from hwbot.handlers import build_router
from hwbot.notify import send_due_reminders
from hwbot.roster import load_roster

logger = logging.getLogger(__name__)


async def reminder_loop(bot: Bot, db: Database, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await send_due_reminders(bot, db)
        except Exception:
            logger.exception("reminder tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=300)
        except TimeoutError:
            continue


async def run_bot(settings: Settings | None = None) -> None:
    settings = settings or load_settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    db = Database(settings.db_path)
    await db.connect()
    try:
        if await db.student_count() == 0:
            await db.seed_roster(load_roster(settings.roster_path))
        bot = Bot(
            token=settings.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        try:
            dispatcher = Dispatcher(storage=MemoryStorage())
            dispatcher.include_router(build_router())
            dispatcher.workflow_data["db"] = db
            dispatcher.workflow_data["settings"] = settings
            await setup_commands(bot, settings)
            stop = asyncio.Event()
            reminder_task = asyncio.create_task(reminder_loop(bot, db, stop))
            try:
                await dispatcher.start_polling(bot)  # pyright: ignore[reportUnknownMemberType]
            finally:
                stop.set()
                reminder_task.cancel()
                try:
                    await reminder_task
                except asyncio.CancelledError:
                    pass
        finally:
            await bot.session.close()
    finally:
        await db.close()
