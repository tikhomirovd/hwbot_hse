from __future__ import annotations

from collections.abc import Sequence

from hwbot.models import Assessment, Submission

SUBMISSION_MARKERS = (
    "http://",
    "https://",
    "github.com",
    "gitlab",
    "git@",
    ".ipynb",
)


def is_issued(assessment: Assessment, now: int) -> bool:
    return assessment.issued_at is None or assessment.issued_at <= now


def is_accept_open(assessment: Assessment, now: int) -> bool:
    return assessment.accept_until_ts is None or now <= assessment.accept_until_ts


def is_current(assessment: Assessment, now: int) -> bool:
    return (
        assessment.active
        and assessment.submit_via_bot
        and is_issued(assessment, now)
        and is_accept_open(assessment, now)
    )


def is_upcoming(assessment: Assessment, now: int) -> bool:
    return (
        assessment.active
        and assessment.submit_via_bot
        and assessment.issued_at is not None
        and now < assessment.issued_at
    )


def current_assessments(
    assessments: Sequence[Assessment], now: int
) -> list[Assessment]:
    return [item for item in assessments if is_current(item, now)]


def upcoming_assessments(
    assessments: Sequence[Assessment], now: int
) -> list[Assessment]:
    return [item for item in assessments if is_upcoming(item, now)]


def looks_like_submission(text: str, *, has_open_work: bool = False) -> bool:
    if has_open_work and text.strip():
        return True
    lowered = text.casefold()
    return any(marker in lowered for marker in SUBMISSION_MARKERS)


def would_lower_cap(
    *,
    deadline_ts: int | None,
    previous: Submission,
    new_submitted_at: int,
    old_cap: float,
    new_cap: float,
) -> bool:
    if deadline_ts is None:
        return False
    if previous.submitted_at > deadline_ts:
        return False
    if new_submitted_at <= deadline_ts:
        return False
    return new_cap < old_cap
