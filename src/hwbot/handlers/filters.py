from __future__ import annotations

from aiogram.filters import Filter
from aiogram.types import Message


def is_plain_text(text: str | None) -> bool:
    if text is None:
        return False
    stripped = text.lstrip()
    return bool(stripped) and not stripped.startswith("/")


class PlainText(Filter):
    async def __call__(self, message: Message) -> bool:
        return is_plain_text(message.text)
