from __future__ import annotations

from hwbot.course import DEFAULT_COURSE_PATH, LateRule, load_course
from hwbot.formatting import (
    accept_confirm_text,
    admin_home_text,
    admin_submission_notice,
    db_access_text,
    format_attendance_full_list,
    format_grade_report,
    format_gradebook_board,
    format_homework_card,
    format_hw_empty_soon,
    format_profile,
    format_status_board,
    format_status_report,
    format_students_report,
    help_text,
    homework_status_for_student,
    late_grade_note,
    late_submit_warning,
    new_homework_announcement,
    register_done,
    short_name,
    submit_button_text,
    week0_one_liner,
)
from hwbot.grading import (
    StudentState,
    build_item,
    build_report,
    count_attendance,
    student_lessons,
)
from hwbot.models import Assessment, HomeworkStatusRow, Student, Submission
from hwbot.telegramutil import escape_html
from hwbot.timeutil import parse_deadline


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
    assert homework_status_for_student(homework, None, 101) == "просрочено"
    assert homework_status_for_student(homework, None, 201) == "приём закрыт · 0"
    submitted = Submission(1, 1, 1, "https://github.com/x", 40)
    assert homework_status_for_student(homework, submitted, 200) == "сдано"


def test_grade_report_november_text() -> None:
    course = load_course(DEFAULT_COURSE_PATH)
    now = parse_deadline("2026-11-21 16:00")
    held = [
        lesson.code
        for lesson in course.lessons
        if lesson.starts_ts <= now
        and (lesson.kind == "lecture" or lesson.seminar_group == "262")
    ]
    absent = {held[0], held[1]}
    hw2 = course.assessment_by_code("hw2")
    assert hw2.deadline_ts is not None
    state = StudentState(
        seminar_group="262",
        attendance={code: ("absent" if code in absent else "present") for code in held},
        held_lesson_codes=frozenset(held),
        submissions={
            "hw1": course.assessment_by_code("hw1").deadline_ts or 0,
            "hw2": hw2.deadline_ts + 300,
            "hw3": now,
            "project1": course.assessment_by_code("project1").deadline_ts or 0,
        },
        grades={"quiz1": 8, "quiz2": 7, "hw1": 9, "hw2": 10, "project1": 8},
    )
    text = format_grade_report(build_report(state, course, now), course)
    assert "оценка на 21 ноября" in text.casefold() or "21 ноября" in text
    assert "8,1" in text
    assert "3,8" in text
    assert "остановиться" in text
    assert "поставлено 10, за просрочку −1, итог 9" in text
    assert "потол" not in text
    assert "ждёт проверки" in text
    assert "<pre>" in text
    assert "перекличк" not in text
    assert "напиши преподавателю" not in text.casefold()


def test_empty_grade_text() -> None:
    course = load_course(DEFAULT_COURSE_PATH)
    report = build_report(
        StudentState(None, {}, frozenset(), {}, {}),
        course,
        parse_deadline("2026-09-06 12:00"),
    )
    text = format_grade_report(report, course)
    assert "считать пока нечего" in text.casefold()
    assert "19 сентября" not in text
    assert "перекличк" not in text
    assert "напиши преподавателю" not in text.casefold()


def test_payload_with_html_is_escaped_in_card() -> None:
    homework = _hw(1000)
    submission = Submission(1, 1, 1, "<b>oops</b>", 40)
    text = format_homework_card(homework, submission, 50)
    assert "<b>oops</b>" not in text
    assert escape_html("<b>oops</b>") in text or "&lt;b&gt;oops&lt;/b&gt;" in text


def test_attendance_matches_grade_counts() -> None:
    course = load_course(DEFAULT_COURSE_PATH)
    now = parse_deadline("2026-10-10 12:00")
    lessons = list(student_lessons(course, "262"))
    held = frozenset(
        lesson.code for lesson in lessons if lesson.starts_ts <= now
    )
    empty = StudentState(None, {}, held, {}, {})
    lectures = list(student_lessons(course, None))
    present, absent, excused = count_attendance(
        lectures,
        empty.attendance,
        held_codes=held,
        missing_as_absent=True,
    )
    report = build_report(
        StudentState(None, {}, held, {}, {}),
        course,
        now,
    )
    assert (present, absent, excused) == (
        report.attendance_present,
        report.attendance_absent,
        report.attendance_excused,
    )
    partial_marks = {next(iter(held)): "present"} if held else {}
    partial = StudentState("262", partial_marks, held, {}, {})
    lessons_b = list(student_lessons(course, "262"))
    present2, absent2, excused2 = count_attendance(
        lessons_b,
        partial.attendance,
        held_codes=held,
        missing_as_absent=True,
    )
    report2 = build_report(partial, course, now)
    assert (present2, absent2, excused2) == (
        report2.attendance_present,
        report2.attendance_absent,
        report2.attendance_excused,
    )


def test_attendance_list_has_three_states() -> None:
    course = load_course(DEFAULT_COURSE_PATH)
    lessons = list(student_lessons(course, "262"))[:4]
    held = frozenset({lessons[0].code, lessons[1].code, lessons[2].code})
    marks = {lessons[0].code: "present", lessons[1].code: "absent"}
    text = format_attendance_full_list(lessons, marks, held)
    assert "был" in text
    assert "не был" in text
    assert "ещё впереди" in text
    assert "перекличк" not in text


def test_empty_hw_explains_week0() -> None:
    homework = _hw(parse_deadline("2026-09-19 23:59"))
    homework = Assessment(
        id=homework.id,
        code=homework.code,
        label=homework.label,
        title=homework.title,
        body=homework.body,
        component=homework.component,
        weight_final=homework.weight_final,
        submit_via_bot=True,
        issued_at=parse_deadline("2026-09-12 12:30"),
        deadline_ts=homework.deadline_ts,
        accept_until_ts=homework.accept_until_ts,
        graded_on_ts=None,
        late_rule=homework.late_rule,
        blocking=False,
        active=True,
    )
    text = format_hw_empty_soon([homework])
    assert "12 сентября" in text
    assert "лекции" in text
    assert "/grade" in text
    line = week0_one_liner([homework])
    assert "ДЗ-1" in line
    student = Student(
        1, "Иванов Иван Иванович", "БАЦРФ261", "ivanov@example.edu", None, None
    )
    done = register_done(student, [homework])
    assert "/mysubmissions" in done
    assert "12 сентября" in done
    profile = format_profile(student, upcoming=[homework], has_current=False)
    assert "12 сентября" in profile


def test_submit_button_and_accept_confirm() -> None:
    homework = _hw(1000)
    assert "Сдать" in submit_button_text(homework, submitted=False)
    assert "Обновить" in submit_button_text(homework, submitted=True)
    text = accept_confirm_text(homework, "https://github.com/a/b")
    assert "Collaborators" in text
    assert "github.com/a/b" in text


def test_exam_late_warning_has_no_daily_cut() -> None:
    exam = Assessment(
        id=11,
        code="exam",
        label="Экзамен",
        title="Защита",
        body="",
        component="exam",
        weight_final=0.30,
        submit_via_bot=True,
        issued_at=1,
        deadline_ts=100,
        accept_until_ts=200,
        graded_on_ts=None,
        late_rule="none",
        blocking=True,
        active=True,
    )
    rule = LateRule("none", 0.0, 0.0, 0, None)
    text = late_submit_warning(exam, 2, rule)
    assert "штрафа за просрочку" in text.casefold()
    assert "минус балл" not in text


def test_late_texts_show_deduction_not_ceiling() -> None:
    course = load_course(DEFAULT_COURSE_PATH)
    rule = course.late_rule_named("homework")
    homework = _hw(parse_deadline("2026-09-27 23:59"))
    warning = late_submit_warning(homework, 2, rule)
    assert "вычтется <b>2 балла</b>" in warning
    assert "не опускает оценку ниже 4" in warning
    assert "вычитается 1 балл" in help_text()
    for text in (warning, help_text()):
        assert "потол" not in text.casefold()


def test_late_grade_note_mentions_floor() -> None:
    course = load_course(DEFAULT_COURSE_PATH)
    rule = course.late_rule_named("homework")
    hw = course.assessment_by_code("hw1")
    assert hw.deadline_ts is not None
    late3 = hw.deadline_ts + 2 * 86400 + 60
    floored = build_item(hw, course, late3, late3, 6)
    assert late_grade_note(floored, rule) == (
        "поставлено 6, за просрочку −3, итог 4: штраф не опускает оценку ниже 4"
    )
    low = build_item(hw, course, late3, late3, 3)
    assert low.applied_score == 3
    assert "итог 3: штраф не опускает" in late_grade_note(low, rule)
    on_time = build_item(hw, course, hw.deadline_ts, hw.deadline_ts, 9)
    assert late_grade_note(on_time, rule) == ""


def test_db_text_only_prime_monitor() -> None:
    student = Student(1, "Иванов Иван Иванович", "БАЦРФ261", "i@edu.hse.ru", None, None)
    text = db_access_text(student)
    assert "prime-monitor" in text
    assert "клона репозитория курса" not in text


def test_announcement_week_only_when_span_is_week() -> None:
    hw = _hw(parse_deadline("2026-09-19 23:59"))
    short = Assessment(
        id=1,
        code="hw1",
        label="ДЗ-1",
        title="ДЗ 1",
        body="",
        component="homework",
        weight_final=0.0625,
        submit_via_bot=True,
        issued_at=parse_deadline("2026-09-12 12:30"),
        deadline_ts=parse_deadline("2026-09-19 23:59"),
        accept_until_ts=hw.accept_until_ts,
        graded_on_ts=None,
        late_rule="homework",
        blocking=False,
        active=True,
    )
    long = Assessment(
        id=2,
        code="project1",
        label="Проект 1",
        title="Записка",
        body="",
        component="project1",
        weight_final=0.20,
        submit_via_bot=True,
        issued_at=parse_deadline("2026-09-19 16:00"),
        deadline_ts=parse_deadline("2026-10-25 23:59"),
        accept_until_ts=parse_deadline("2026-11-01 23:59"),
        graded_on_ts=None,
        late_rule="project1",
        blocking=False,
        active=True,
    )
    assert "это неделя" in new_homework_announcement(short)
    assert "это неделя" not in new_homework_announcement(long)


def test_students_report_lists_registered_by_group() -> None:
    bound = Student(1, "Иванов Иван Иванович", "БАЦРФ261", "ivanov@example.edu", 1, "a")
    free = Student(2, "Петрова Анна Сергеевна", "БАЦРФ262", "petrova@example.edu", None, None)
    text = format_students_report([bound, free], registered=True)
    assert "Зарегистрировано 1 из 2" in text
    assert "БАЦРФ261" in text
    assert "Иванов Иван Иванович" in text
    assert "Петрова" not in text
    missing = format_students_report([bound, free], registered=False)
    assert "Не зарегистрированы: 1 из 2" in missing
    assert "Петрова Анна Сергеевна" in missing
    assert "Иванов" not in missing
    empty = format_students_report([free], registered=True)
    assert "Пока никто не зашёл" in empty
    full = format_students_report([bound], registered=False)
    assert "Все уже в боте" in full


def _student() -> Student:
    return Student(1, "Иванов Иван Иванович", "БАЦРФ261", "ivanov@example.edu", 1, "a")


def test_admin_submission_notice_first_and_update() -> None:
    homework = _hw(parse_deadline("2026-09-27 23:59"))
    student = _student()
    on_time = Submission(1, 1, 1, "https://github.com/x/hw", homework.deadline_ts or 1)
    text = admin_submission_notice(student, homework, on_time, previous=None)
    assert "Иванов Иван Иванович (БАЦРФ261) сдал <b>ДЗ-1</b>" in text
    assert '<a href="https://github.com/x/hw">' in text
    assert "в срок" in text
    assert "обновил" not in text

    late = Submission(2, 1, 1, "просто текст", (homework.deadline_ts or 1) + 86400)
    updated = admin_submission_notice(student, homework, late, previous=on_time)
    assert "обновил <b>ДЗ-1</b>" in updated
    assert "просто текст" in updated
    assert "<a " not in updated
    assert "на 1 день позже дедлайна" in updated


def test_format_status_report_lists_payloads() -> None:
    homework = _hw(100)
    done = _student()
    missing = Student(2, "Петрова Анна Сергеевна", "БАЦРФ261", "p@example.edu", None, None)
    rows = [
        HomeworkStatusRow(
            student=done,
            submission=Submission(1, 1, 1, "https://github.com/x/hw", 40),
            accept_until_ts=homework.accept_until_ts,
            now_ts=50,
        ),
        HomeworkStatusRow(student=missing, submission=None),
    ]
    text = format_status_report(homework, rows, html=False)
    assert "Сдали: 1 / 2 · не сдали: 1" in text
    assert "Иванов Иван Иванович (БАЦРФ261)" in text
    assert "github.com/x/hw" in text
    assert "в срок" in text
    assert "Петрова Анна Сергеевна" in text
    html = format_status_report(homework, rows, html=True)
    assert '<a href="https://github.com/x/hw">' in html
    assert "<b>Сдали:</b>" in html


def test_format_status_board_marks_submitted_homeworks() -> None:
    hw1 = _hw(100)
    hw2 = Assessment(
        id=2,
        code="hw2",
        label="ДЗ-2",
        title="ДЗ 2",
        body="body",
        component="homework",
        weight_final=0.0625,
        submit_via_bot=True,
        issued_at=1,
        deadline_ts=200,
        accept_until_ts=200 + 7 * 86400,
        graded_on_ts=None,
        late_rule="homework",
        blocking=False,
        active=True,
    )
    ivanov = _student()
    petrova = Student(2, "Петрова Анна Сергеевна", "БАЦРФ261", "p@example.edu", None, None)
    latest = {(1, 1): Submission(1, 1, 1, "https://github.com/x", 40)}
    text = format_status_board([hw1, hw2], [ivanov, petrova], latest, html=False)
    assert "ДЗ-1 1/2 · ДЗ-2 0/2" in text
    assert "Иванов Иван Иванович (261)  ДЗ-1 ✓  ДЗ-2 —" in text
    assert "Петрова Анна Сергеевна (261)  ДЗ-1 —  ДЗ-2 —" in text
    assert "/status hw1" in text
    assert "/status hw2" in text
    assert format_status_board([], [ivanov], {}, html=False) == "ДЗ ещё нет."


def test_admin_home_mentions_status_summary() -> None:
    text = admin_home_text()
    assert "/status — кто какие ДЗ сдал" in text
    assert "/status hw1" in text


def test_gradebook_board() -> None:
    course = load_course(DEFAULT_COURSE_PATH)
    now = parse_deadline("2026-09-20 12:00")
    held = frozenset({"L01", "L02"})

    def state(seminar: str, present: bool, quiz: float) -> StudentState:
        status = "present" if present else "absent"
        return StudentState(
            seminar, {"L01": status, "L02": status}, held, {}, {"quiz1": quiz}
        )

    good = Student(2, "Яковлев Пётр Ильич", "БАЦРФ262", "ya@edu.hse.ru", None, None, "262")
    weak = Student(1, "Антонов Иван Петрович", "БАЦРФ261", "an@edu.hse.ru", None, None, "261")
    rows = [
        (good, build_report(state("262", True, 10.0), course, now)),
        (weak, build_report(state("261", False, 2.0), course, now)),
    ]
    lines = format_gradebook_board(rows)
    assert lines[0].startswith("студент")
    # по алфавиту, а не в порядке списка; имя сжато до инициалов
    assert lines[1].startswith("Антонов И.П.")
    assert lines[2].startswith("Яковлев П.И.")
    # кто ходил и написал на 10 — выше того, кто не ходил и написал на 2
    heading_good, heading_weak = rows[0][1].heading_to, rows[1][1].heading_to
    assert heading_good is not None and heading_weak is not None
    assert heading_good > heading_weak
    assert any("студентов 2" in line and "ниже 4" in line for line in lines)
    # десятичный разделитель — запятая, как во всех текстах бота
    numbers = lines[1].split()[-3:]
    assert numbers == ["1,2", "0,0", "0,0"]
    # строка влезает в телефон
    assert max(len(line) for line in lines) < 46


def test_short_name() -> None:
    assert short_name("Абрамова Анастасия Романовна") == "Абрамова А.Р."
    assert short_name("Сорокина София") == "Сорокина С."
    assert short_name("Рубан") == "Рубан"
