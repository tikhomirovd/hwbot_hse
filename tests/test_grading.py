from __future__ import annotations

from hwbot.course import DEFAULT_COURSE_PATH, LateRule, load_course
from hwbot.grading import (
    ItemResult,
    ItemStatus,
    Mode,
    StudentState,
    accumulated,
    attendance_score,
    build_item,
    build_report,
    component_score,
    days_late,
    final_score,
    late_cap,
    round_half_up,
)
from hwbot.timeutil import parse_deadline


COURSE = load_course(DEFAULT_COURSE_PATH)
SCALE = COURSE.attendance_scale
HW_RULE = LateRule("homework", 1.0, 4.0, 7, 7)
PROJECT_RULE = LateRule("project1", 1.0, 0.0, 0, None)


def test_attendance_table() -> None:
    expected = {
        24: (100, 10),
        23: (96, 9),
        22: (92, 8),
        21: (88, 7),
        20: (83, 5),
        19: (79, 4),
        18: (75, 3),
        17: (71, 2),
        16: (67, 1),
        0: (0, 0),
    }
    for present, (percent, score) in expected.items():
        absent = 24 - present
        got = attendance_score(present, absent, 0, SCALE)
        assert got == score, f"present={present}: expected {score}, got {got}"
        if present == 0:
            continue
        from hwbot.grading import attendance_percent

        assert attendance_percent(present, absent, 0) == percent


def test_six_is_unreachable() -> None:
    scores = {attendance_score(present, 24 - present, 0, SCALE) for present in range(1, 25)}
    assert 6 not in scores


def test_zero_and_empty_denominator() -> None:
    assert attendance_score(0, 24, 0, SCALE) == 0
    assert attendance_score(0, 0, 0, SCALE) is None
    assert attendance_score(0, 0, 5, SCALE) is None


def test_excused_leave_denominator() -> None:
    assert attendance_score(20, 2, 2, SCALE) == 8


def test_days_late_boundary() -> None:
    deadline = parse_deadline("2026-09-19 23:59")
    submitted = parse_deadline("2026-09-20 00:05")
    assert days_late(submitted, deadline) == 1
    assert days_late(deadline, deadline) == 0
    assert days_late(deadline - 1, deadline) == 0


def test_late_caps() -> None:
    homework = {0: 10, 1: 9, 3: 7, 6: 4, 7: 4, 8: 0, 12: 0}
    project = {0: 10, 1: 9, 3: 7, 6: 4, 7: 3, 8: 2, 12: 0}
    for days, cap in homework.items():
        assert late_cap(HW_RULE, days) == cap
    for days, cap in project.items():
        assert late_cap(PROJECT_RULE, days) == cap


def test_cap_clips_score() -> None:
    from hwbot.grading import apply_late

    assert apply_late(9, 4) == 4
    assert apply_late(3, 4) == 3


def test_exam_blocking() -> None:
    assert final_score(6.8, 3, COURSE) == 3
    assert final_score(6.8, 4, COURSE) == 8.0
    assert final_score(7.0, 10, COURSE) == 10.0
    assert final_score(6.8, None, COURSE) is None


def test_round_half_up_not_bankers() -> None:
    assert round_half_up(6.5) == 7
    assert round_half_up(7.5) == 8
    assert round(6.5) == 6


def _item(
    code: str,
    weight: float,
    status: ItemStatus,
    score: float | None,
    component: str = "homework",
) -> ItemResult:
    return ItemResult(
        code=code,
        label=code,
        component=component,
        weight_final=weight,
        status=status,
        raw_score=score,
        cap=10.0 if score is not None else None,
        applied_score=score,
        days_late=0,
        submitted_at=None,
    )


def test_awaiting_excluded_from_heading() -> None:
    items = (
        _item("hw1", 0.0625, ItemStatus.GRADED, 10),
        _item("hw2", 0.0625, ItemStatus.AWAITING, None),
        _item("hw3", 0.0625, ItemStatus.OPEN, None),
        _item("hw4", 0.0625, ItemStatus.UPCOMING, None),
    )
    now_score = component_score(items, Mode.NOW)
    zero_score = component_score(items, Mode.FILL_ZERO)
    assert now_score == 10
    assert zero_score == 2.5


def test_empty_report() -> None:
    state = StudentState(
        seminar_group="262",
        attendance={},
        held_lesson_codes=frozenset(),
        submissions={},
        grades={},
    )
    now = parse_deadline("2026-09-06 12:00")
    report = build_report(state, COURSE, now)
    assert report.heading_to is None
    assert report.in_pocket is None
    assert report.if_nothing is None


def test_attendance_only_heading() -> None:
    lessons = [item.code for item in COURSE.lessons if item.kind == "lecture"]
    held = frozenset(lessons[:2])
    state = StudentState(
        seminar_group=None,
        attendance={lessons[0]: "present", lessons[1]: "present"},
        held_lesson_codes=held,
        submissions={},
        grades={},
    )
    now = parse_deadline("2026-09-06 12:00")
    report = build_report(state, COURSE, now)
    assert report.attendance_score_now == 10
    assert report.heading_to == 10


def _nov21_state() -> tuple[StudentState, int]:
    now = parse_deadline("2026-11-21 16:00")
    held: list[str] = []
    for lesson in COURSE.lessons:
        if lesson.starts_ts > now:
            continue
        if lesson.kind == "lecture":
            held.append(lesson.code)
        elif lesson.seminar_group == "262":
            held.append(lesson.code)
    assert len(held) == 18
    absent = {held[0], held[1]}
    attendance = {
        code: ("absent" if code in absent else "present") for code in held
    }
    hw2 = COURSE.assessment_by_code("hw2")
    assert hw2.deadline_ts is not None
    state = StudentState(
        seminar_group="262",
        attendance=attendance,
        held_lesson_codes=frozenset(held),
        submissions={
            "hw1": COURSE.assessment_by_code("hw1").deadline_ts or 0,
            "hw2": hw2.deadline_ts + 300,
            "hw3": now,
            "project1": COURSE.assessment_by_code("project1").deadline_ts or 0,
        },
        grades={
            "quiz1": 8,
            "quiz2": 7,
            "hw1": 9,
            "hw2": 10,
            "project1": 8,
        },
    )
    return state, now


def test_three_numbers_november_example() -> None:
    state, now = _nov21_state()
    report = build_report(state, COURSE, now)
    assert report.attendance_score_now == 7
    assert report.attendance_present == 16
    assert report.attendance_held == 18
    quizzes = next(line for line in report.components if line.key == "quizzes")
    homework = next(line for line in report.components if line.key == "homework")
    project = next(line for line in report.components if line.key == "project1")
    assert quizzes.score_now == 7.5
    assert homework.score_now == 9.0
    assert project.score_now == 8.0
    hw2 = next(item for item in report.items if item.code == "hw2")
    assert hw2.raw_score == 10
    assert hw2.applied_score == 9
    assert hw2.days_late == 1
    hw3 = next(item for item in report.items if item.code == "hw3")
    assert hw3.status is ItemStatus.AWAITING
    quiz3 = next(item for item in report.items if item.code == "quiz3")
    assert quiz3.status is ItemStatus.AWAITING
    assert report.heading_to is not None
    assert round_half_up(report.heading_to, 1) == 8.1
    assert report.in_pocket is not None
    assert round_half_up(report.in_pocket, 1) == 3.8
    assert report.if_nothing == 3.0
    assert report.exam_blocked


def test_build_item_late_cap() -> None:
    hw = COURSE.assessment_by_code("hw1")
    assert hw.deadline_ts is not None
    item = build_item(hw, COURSE, hw.deadline_ts + 300, hw.deadline_ts + 300, 9)
    assert item.days_late == 1
    assert item.cap == 9
    assert item.applied_score == 9


def test_accumulated_uses_course_cap() -> None:
    assert accumulated(
        {"attendance": 10, "quizzes": 10, "homework": 10, "project1": 10},
        COURSE,
    ) == 7.0
