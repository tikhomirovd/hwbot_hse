from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

from hwbot.config import Settings

logger = logging.getLogger(__name__)

STUDENT_COMMANDS = [
    BotCommand(command="start", description="Профиль и регистрация"),
    BotCommand(command="hw", description="Активные ДЗ"),
    BotCommand(command="submit", description="Сдать ДЗ"),
    BotCommand(command="mysubmissions", description="Мои сдачи"),
    BotCommand(command="help", description="Помощь"),
    BotCommand(command="cancel", description="Отменить ввод"),
]

ADMIN_COMMANDS = [
    *STUDENT_COMMANDS,
    BotCommand(command="newhw", description="Новое ДЗ"),
    BotCommand(command="status", description="Кто сдал"),
    BotCommand(command="export", description="Выгрузить CSV"),
    BotCommand(command="missing", description="Кто не сдал"),
]


async def setup_commands(bot: Bot, settings: Settings) -> None:
    await bot.set_my_commands(STUDENT_COMMANDS, scope=BotCommandScopeDefault())
    for admin_id in settings.admin_telegram_ids:
        try:
            await bot.set_my_commands(
                ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id)
            )
        except TelegramBadRequest:
            logger.warning("admin chat is not ready yet, skip scoped commands")
