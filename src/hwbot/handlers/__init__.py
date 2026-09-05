from __future__ import annotations

from aiogram import Router

from hwbot.handlers.admin import router as admin_router
from hwbot.handlers.student import fallback_router
from hwbot.handlers.student import router as student_router


def build_router() -> Router:
    root = Router()
    root.include_router(student_router)
    root.include_router(admin_router)
    root.include_router(fallback_router)
    return root
