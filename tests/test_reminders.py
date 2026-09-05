from __future__ import annotations

from hwbot.models import Assessment, Student, Submission
from hwbot.reminders import (
    WINDOW_12H,
    WINDOW_24H,
    WINDOW_ACCEPT_CLOSING,
    WINDOW_DEADLINE_PASSED,
    collect_reminder_targets,
    reminder_window,
)


def _student(student_id: int, telegram_id: int | None = 10) -> Student:
    return Student(
        id=student_id,
        full_name="Тест Тестов",
        group_code="БАЦРФ261",
        email="t@edu.hse.ru",
        telegram_id=telegram_id,
        telegram_username="t",
    )


def _hw(deadline_ts: int) -> Assessment:
    return Assessment(
        id=1,
        code="hw1",
        label="ДЗ-1",
        title="ДЗ 1",
        body="body",
        component="homework",
        weight_final=0.0625,
        submit_via_bot=True,
        issued_at=1,
        deadline_ts=deadline_ts,
        accept_until_ts=deadline_ts + 7 * 86400,
        graded_on_ts=None,
        late_rule="homework",
        blocking=False,
        active=True,
    )


def test_windows() -> None:
    deadline = 100_000
    assert reminder_window(deadline, deadline - 20 * 3600) == WINDOW_24H
    assert reminder_window(deadline, deadline - 10 * 3600) == WINDOW_12H
    assert reminder_window(deadline, deadline - 30 * 3600) is None
    assert reminder_window(deadline, deadline + 1) is None


def test_skip_submitted_and_already_sent() -> None:
    now = 50_000
    homework = _hw(now + 20 * 3600)
    student = _student(5)
    targets = collect_reminder_targets(
        [homework],
        [student],
        latest_by_pair={(1, 5): Submission(1, 5, 1, "x", now)},
        sent=set(),
        now_ts=now,
    )
    assert targets == []
    targets = collect_reminder_targets(
        [homework],
        [student],
        latest_by_pair={},
        sent={(1, 5, WINDOW_24H)},
        now_ts=now,
    )
    assert targets == []
    targets = collect_reminder_targets(
        [homework],
        [student],
        latest_by_pair={},
        sent=set(),
        now_ts=now,
    )
    assert len(targets) == 1
    assert targets[0].window == WINDOW_24H


def test_deadline_passed_and_accept_closing() -> None:
    deadline = 100_000
    homework = _hw(deadline)
    student = _student(5)
    now = deadline + 10
    targets = collect_reminder_targets(
        [homework], [student], {}, set(), now_ts=now
    )
    windows = {target.window for target in targets}
    assert WINDOW_DEADLINE_PASSED in windows
    closing_now = homework.accept_until_ts - 10 * 3600 if homework.accept_until_ts else now
    assert homework.accept_until_ts is not None
    targets = collect_reminder_targets(
        [homework], [student], {}, set(), now_ts=closing_now
    )
    windows = {target.window for target in targets}
    assert WINDOW_ACCEPT_CLOSING in windows
    assert WINDOW_DEADLINE_PASSED in windows


def test_unregistered_students_are_skipped() -> None:
    now = 50_000
    targets = collect_reminder_targets(
        [_hw(now + 5 * 3600)],
        [_student(5, telegram_id=None)],
        latest_by_pair={},
        sent=set(),
        now_ts=now,
    )
    assert targets == []
