from __future__ import annotations

from hwbot.models import Student
from hwbot.telegramutil import (
    display_payload,
    escape_html,
    payload_html,
    split_long_text,
    telegram_contact_html,
)


def _student(
    *,
    telegram_id: int | None = 1,
    telegram_username: str | None = "ivanov",
) -> Student:
    return Student(
        1,
        "Иванов Иван Иванович",
        "БАЦРФ261",
        "ivanov@example.edu",
        telegram_id,
        telegram_username,
    )


def test_telegram_contact_prefers_username() -> None:
    assert telegram_contact_html(_student()) == "@ivanov"
    assert telegram_contact_html(_student(telegram_username="@ivanov")) == "@ivanov"
    assert telegram_contact_html(_student(telegram_id=None)) == "@ivanov"


def test_telegram_contact_falls_back_to_id_link() -> None:
    html = telegram_contact_html(_student(telegram_username=None))
    assert html == '<a href="tg://user?id=1">написать в личку</a>'
    assert telegram_contact_html(_student(telegram_id=None, telegram_username=None)) == ""
    assert telegram_contact_html(_student(telegram_username="  ")) == (
        '<a href="tg://user?id=1">написать в личку</a>'
    )


def test_escape_payload_does_not_keep_html() -> None:
    escaped = escape_html("<b>oops</b>")
    assert "<b>" not in escaped
    assert "&lt;b&gt;oops&lt;/b&gt;" == escaped


def test_display_payload_strips_scheme_and_escapes() -> None:
    assert display_payload("https://github.com/x/<b>") == "github.com/x/&lt;b&gt;"


def test_payload_html_makes_http_url_clickable() -> None:
    html = payload_html("https://github.com/x/<b>")
    assert html.startswith('<a href="https://github.com/x/&lt;b&gt;">')
    assert "github.com/x/&lt;b&gt;" in html
    assert payload_html("просто текст <b>") == "просто текст &lt;b&gt;"
    assert "<a " not in payload_html("просто текст")


def test_split_long_keeps_pre_intact() -> None:
    pre = "<pre>" + ("x" * 100) + "</pre>"
    prefix = "A\n\n"
    suffix = "\n\nB " + ("y" * 3400)
    text = prefix + pre + suffix
    parts = split_long_text(text, limit=3500)
    assert len(parts) >= 2
    assert any("<pre>" in part and "</pre>" in part for part in parts)
    joined = "".join(parts)
    assert pre in joined
    for part in parts:
        if "<pre>" in part:
            start = part.index("<pre>")
            end = part.index("</pre>")
            assert end > start


def test_split_long_over_4096_is_valid_chunks() -> None:
    paragraph = "абзац " * 200
    text = "\n\n".join([paragraph] * 8)
    assert len(text) > 4096
    parts = split_long_text(text, limit=3500)
    assert len(parts) >= 2
    assert all(len(part) <= 3500 or "<pre>" in part for part in parts)
    assert "".join(parts).replace("\n\n", "") == text.replace("\n\n", "")
