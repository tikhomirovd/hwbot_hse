from __future__ import annotations

from hwbot.formatting import homework_status_for_student
from hwbot.models import Assessment, Submission


def _hw(deadline: int, accept_until: int | None = None) -> Assessment:
    close = deadline + 7 * 86400 if accept_until is None else accept_until
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
        deadline_ts=deadline,
        accept_until_ts=close,
        graded_on_ts=None,
        late_rule="homework",
        blocking=False,
        active=True,
    )


def test_status_labels() -> None:
    homework = _hw(100, accept_until=200)
    assert homework_status_for_student(homework, None, 50) == "не сдано"
    assert "просрочено" in homework_status_for_student(homework, None, 101)
    assert homework_status_for_student(homework, None, 201) == "приём закрыт · 0"
    submitted = Submission(1, 1, 1, "https://github.com/x", 40)
    assert homework_status_for_student(homework, submitted, 200) == "сдано"
