from __future__ import annotations

import html
import re
from typing import Any

from aiogram.types import Message

from hwbot.models import Student

MESSAGE_LIMIT = 3500
_PRE_BLOCK = re.compile(r"(<pre(?:\s[^>]*)?>.*?</pre>)", re.DOTALL | re.IGNORECASE)


def escape_html(value: str) -> str:
    return html.escape(value, quote=True)


def telegram_contact_html(student: Student) -> str:
    """Контакт студента для админского уведомления: @username или ссылка по id."""
    username = (student.telegram_username or "").strip().lstrip("@")
    if username:
        return f"@{escape_html(username)}"
    if student.telegram_id is not None:
        return f'<a href="tg://user?id={student.telegram_id}">написать в личку</a>'
    return ""


def payload_plain(payload: str) -> str:
    text = payload.strip()
    lowered = text.casefold()
    if lowered.startswith("https://"):
        return text[8:]
    if lowered.startswith("http://"):
        return text[7:]
    return text


def display_payload(payload: str) -> str:
    return escape_html(payload_plain(payload))


def payload_html(payload: str) -> str:
    text = payload.strip()
    lowered = text.casefold()
    if lowered.startswith("https://") or lowered.startswith("http://"):
        return f'<a href="{escape_html(text)}">{display_payload(text)}</a>'
    return display_payload(text)


def split_long_text(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    buf = ""
    for piece in _atomic_pieces(text):
        if not buf:
            if len(piece) <= limit or _is_pre(piece):
                buf = piece
                continue
            for part in _split_plain(piece, limit):
                chunks.append(part)
            continue
        candidate = f"{buf}{piece}"
        if len(candidate) <= limit:
            buf = candidate
            continue
        chunks.append(buf)
        if len(piece) <= limit or _is_pre(piece):
            buf = piece.lstrip("\n")
            continue
        parts = _split_plain(piece.lstrip("\n"), limit)
        chunks.extend(parts[:-1])
        buf = parts[-1] if parts else ""
    if buf:
        chunks.append(buf)
    return chunks or [text]


def _is_pre(piece: str) -> bool:
    stripped = piece.strip()
    return stripped.startswith("<pre") and stripped.endswith("</pre>")


def _atomic_pieces(text: str) -> list[str]:
    pieces: list[str] = []
    for block in _PRE_BLOCK.split(text):
        if not block:
            continue
        if _is_pre(block):
            pieces.append(block)
            continue
        parts = block.split("\n\n")
        for index, part in enumerate(parts):
            if index == 0:
                pieces.append(part)
            else:
                pieces.append(f"\n\n{part}")
    return pieces


def _split_plain(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text] if text else []
    lines = text.split("\n")
    chunks: list[str] = []
    buf = ""
    for line in lines:
        addition = line if not buf else f"{buf}\n{line}"
        if len(addition) <= limit:
            buf = addition
            continue
        if buf:
            chunks.append(buf)
        if len(line) <= limit:
            buf = line
            continue
        for start in range(0, len(line), limit):
            chunks.append(line[start : start + limit])
        buf = ""
    if buf:
        chunks.append(buf)
    return chunks


async def answer_long(
    message: Message,
    text: str,
    reply_markup: Any | None = None,
) -> None:
    parts = split_long_text(text)
    last = len(parts) - 1
    for index, part in enumerate(parts):
        markup = reply_markup if index == last else None
        await message.answer(part, reply_markup=markup)
