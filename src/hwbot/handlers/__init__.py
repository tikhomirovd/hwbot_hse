from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import ErrorEvent, Message

from hwbot.formatting import crashed_text
from hwbot.handlers.admin import router as admin_router
from hwbot.handlers.student import fallback_router
from hwbot.handlers.student import router as student_router

logger = logging.getLogger(__name__)


async def on_error(event: ErrorEvent) -> None:
    logger.exception("handler failed")
    update = event.update
    target: Message | None = None
    if update.message is not None:
        target = update.message
    elif update.callback_query is not None:
        message = update.callback_query.message
        if isinstance(message, Message):
            target = message
    if target is None:
        return
    try:
        await target.answer(crashed_text())
    except Exception:
        logger.exception("failed to send crash text")


def build_router() -> Router:
    root = Router()
    root.error.register(on_error)
    root.include_router(student_router)
    root.include_router(admin_router)
    root.include_router(fallback_router)
    return root
