from __future__ import annotations

from hwbot.availability import (
    current_assessments,
    is_current,
    looks_like_submission,
    upcoming_assessments,
    would_lower_cap,
)
from hwbot.formatting import fallback_generic, fallback_reply
from hwbot.models import Assessment, Submission
from hwbot.timeutil import parse_deadline


def _hw(
    *,
    issued: int | None,
    deadline: int,
    accept: int,
    code: str = "hw1",
    label: str = "ДЗ-1",
) -> Assessment:
    return Assessment(
        id=1,
        code=code,
        label=label,
        title="ДЗ 1",
        body="body",
        component="homework",
        weight_final=0.0625,
        submit_via_bot=True,
        issued_at=issued,
        deadline_ts=deadline,
        accept_until_ts=accept,
        graded_on_ts=None,
        late_rule="homework",
        blocking=False,
        active=True,
    )


def test_future_issued_not_current() -> None:
    issued = parse_deadline("2026-09-12 12:30")
    now = parse_deadline("2026-09-05 12:00")
    homework = _hw(issued=issued, deadline=issued + 7 * 86400, accept=issued + 14 * 86400)
    exam = _hw(
        issued=parse_deadline("2026-11-07 14:20"),
        deadline=parse_deadline("2026-12-06 23:59"),
        accept=parse_deadline("2026-12-12 23:59"),
        code="exam",
        label="Экзамен",
    )
    items = [homework, exam]
    assert current_assessments(items, now) == []
    soon = upcoming_assessments(items, now)
    assert [item.code for item in soon] == ["hw1", "exam"]
    assert not is_current(homework, now)


def test_current_after_issue_before_accept() -> None:
    issued = parse_deadline("2026-09-12 12:30")
    now = parse_deadline("2026-09-13 10:00")
    homework = _hw(issued=issued, deadline=issued + 7 * 86400, accept=issued + 14 * 86400)
    assert is_current(homework, now)
    closed = parse_deadline("2026-09-27 00:00")
    assert not is_current(homework, closed)


def test_looks_like_submission() -> None:
    assert looks_like_submission("https://github.com/a/b", has_open_work=False)
    assert looks_like_submission("github.com/a/b", has_open_work=False)
    assert looks_like_submission("notebook.ipynb", has_open_work=False)
    assert looks_like_submission("git@github.com:a/b.git", has_open_work=False)
    assert looks_like_submission("просто длинный текст сдачи", has_open_work=True)
    assert looks_like_submission("ок", has_open_work=True)
    assert not looks_like_submission("просто так", has_open_work=False)


def test_fallback_reply_never_empty() -> None:
    homework = _hw(
        issued=1,
        deadline=100,
        accept=200,
    )
    text = fallback_reply("https://github.com/a/b", [homework])
    assert text
    assert "ДЗ-1" in text
    plain = fallback_reply("готово, смотри репо", [homework])
    assert "ДЗ-1" in plain
    generic = fallback_reply("привет", [])
    assert generic == fallback_generic()


def test_resubmit_after_deadline_lowers_cap() -> None:
    previous = Submission(1, 1, 1, "https://github.com/x", 50)
    assert would_lower_cap(
        deadline_ts=100,
        previous=previous,
        new_submitted_at=200,
        old_cap=10.0,
        new_cap=9.0,
    )
    assert not would_lower_cap(
        deadline_ts=100,
        previous=previous,
        new_submitted_at=80,
        old_cap=10.0,
        new_cap=10.0,
    )
    late_already = Submission(2, 1, 1, "https://github.com/x", 150)
    assert not would_lower_cap(
        deadline_ts=100,
        previous=late_already,
        new_submitted_at=200,
        old_cap=9.0,
        new_cap=8.0,
    )
