from __future__ import annotations

from hwbot.telegramutil import display_payload, escape_html, split_long_text


def test_escape_payload_does_not_keep_html() -> None:
    escaped = escape_html("<b>oops</b>")
    assert "<b>" not in escaped
    assert "&lt;b&gt;oops&lt;/b&gt;" == escaped


def test_display_payload_strips_scheme_and_escapes() -> None:
    assert display_payload("https://github.com/x/<b>") == "github.com/x/&lt;b&gt;"


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
