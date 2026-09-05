from __future__ import annotations

from datetime import datetime

from hwbot.course import Course, Lesson as CourseLesson
from hwbot.grading import GradeReport, ItemResult, ItemStatus, round_half_up
from hwbot.models import Assessment, Student, Submission
from hwbot.timeutil import format_dt, format_remaining, is_deadline_open, zone

MONTHS_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def homework_status_for_student(
    assessment: Assessment,
    submission: Submission | None,
    now_ts: int,
) -> str:
    close_ts = assessment.accept_until_ts
    deadline_ts = assessment.deadline_ts
    if submission is not None:
        if (
            deadline_ts is not None
            and submission.submitted_at > deadline_ts
            and close_ts is not None
            and submission.submitted_at <= close_ts
        ):
            return "сдано с опозданием"
        return "сдано"
    if close_ts is not None and is_deadline_open(close_ts, now_ts):
        if deadline_ts is not None and now_ts > deadline_ts:
            return f"просрочено · принимается до {format_dt(close_ts)}"
        return "не сдано"
    if close_ts is not None:
        return "приём закрыт · 0"
    if deadline_ts is not None and is_deadline_open(deadline_ts, now_ts):
        return "не сдано"
    return "приём закрыт · 0"


def format_homework_card(
    assessment: Assessment,
    submission: Submission | None,
    now_ts: int,
) -> str:
    status = homework_status_for_student(assessment, submission, now_ts)
    lines = [
        f"#{assessment.id} {assessment.label} · {assessment.title}",
    ]
    if assessment.deadline_ts is not None:
        lines.append(
            f"Дедлайн: {format_dt(assessment.deadline_ts)} "
            f"({format_remaining(assessment.deadline_ts, now_ts)})"
        )
    if assessment.accept_until_ts is not None:
        lines.append(f"Приём до: {format_dt(assessment.accept_until_ts)}")
    lines.extend(
        [
            f"Статус: {status}",
            "",
            assessment.body,
        ]
    )
    if submission is not None:
        lines.extend(["", f"Твоя сдача: {submission.payload}"])
    return "\n".join(lines)


def format_profile(student: Student) -> str:
    return (
        f"Ты {student.full_name}, группа {student.group_code}.\n\n"
        "/hw — активные ДЗ\n"
        "/submit — сдать ДЗ\n"
        "/mysubmissions — мои сдачи\n"
        "/grade — оценка\n"
        "/attendance — посещаемость\n"
        "/help — как пользоваться"
    )


def help_text() -> str:
    return (
        "Это бот для сдачи ДЗ и оценки.\n\n"
        "/start — регистрация или профиль\n"
        "/hw — какие работы сейчас принимаются\n"
        "/submit — отправить ссылку на GitHub или текст\n"
        "/mysubmissions — что уже сдал\n"
        "/grade — оценка и разбивка\n"
        "/attendance — посещаемость\n\n"
        "После дедлайна сдать ещё можно в течение недели: "
        "за каждые начатые сутки минус 1 балл, ниже 4 не опустимся. "
        "Позже приём закроется, будет 0. Пересдать можно, пока приём открыт."
    )


def format_hw_list(cards: list[str]) -> str:
    if not cards:
        return "Сейчас нет активных ДЗ."
    return "\n\n———\n\n".join(cards)


def format_score(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "—"
    rounded = round_half_up(value, digits)
    text = f"{rounded:.{digits}f}"
    return text.replace(".", ",")


def format_day_month(ts: int) -> str:
    moment = datetime.fromtimestamp(ts, zone())
    return f"{moment.day} {MONTHS_GENITIVE[moment.month - 1]}"


def format_short_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, zone()).strftime("%d.%m")


def _days_word(days: int) -> str:
    if days % 10 == 1 and days % 100 != 11:
        return "день"
    if days % 10 in {2, 3, 4} and days % 100 not in {12, 13, 14}:
        return "дня"
    return "дней"


def format_late_warning(days: int, cap: float, rule_floor: float | None = None) -> str:
    _ = rule_floor
    return (
        f"Дедлайн уже прошёл. Сейчас потолок {format_score(cap)} "
        f"за {days} {_days_word(days)} просрочки."
    )


def _item_reason(item: ItemResult, course: Course, now: int) -> str:
    assessment = course.assessment_by_code(item.code)
    if item.status is ItemStatus.GRADED:
        if item.days_late > 0 and item.raw_score is not None and item.applied_score is not None:
            if item.raw_score != item.applied_score:
                return (
                    f"поставлено {format_score(item.raw_score, 0 if item.raw_score == int(item.raw_score) else 1)}, "
                    f"срезано до {format_score(item.applied_score)} за {item.days_late} "
                    f"{_days_word(item.days_late)} просрочки"
                )
            return (
                f"сдано с опозданием на {item.days_late} {_days_word(item.days_late)}, "
                f"потолок {format_score(item.cap)}"
            )
        return ""
    if item.status is ItemStatus.AWAITING:
        if assessment.submit_via_bot:
            return "сдано, ждёт проверки"
        if assessment.graded_on_ts is not None:
            graded_day = format_short_date(assessment.graded_on_ts)
            today = format_short_date(now)
            if graded_day == today:
                return "написана сегодня, ждёт проверки"
        return "ждёт проверки"
    if item.status is ItemStatus.MISSED:
        return "не сдано, приём закрыт"
    if item.status is ItemStatus.OPEN:
        if assessment.deadline_ts is not None and now > assessment.deadline_ts:
            return f"принимается до {format_dt(assessment.accept_until_ts or assessment.deadline_ts)}"
        return "приём открыт"
    if assessment.submit_via_bot and assessment.issued_at is not None:
        return f"выдадут {format_short_date(assessment.issued_at)}"
    if assessment.graded_on_ts is not None:
        return f"пишем {format_short_date(assessment.graded_on_ts)}"
    return "ещё не выдано"


def format_grade_report(report: GradeReport, course: Course) -> str:
    if report.heading_to is None and report.in_pocket is None:
        lines = [
            "Пока нечего считать: ни одной оценки и ни одной переклички",
        ]
        if report.seminar_group is None:
            lines.append(
                "Семинарская группа не указана, посещаемость считаю только по лекциям. "
                "Напиши преподавателю"
            )
        return "\n".join(lines)
    lines = [
        f"Оценка на {format_day_month(report.as_of_ts)}",
        "",
        f"Идёшь на {format_score(report.heading_to)} из 10 — по тому, что уже проверено",
        f"В кармане {format_score(report.in_pocket)} из 7 накопленной",
    ]
    if_nothing = f"Если дальше ничего не сдавать: {format_score(report.if_nothing)}"
    if report.exam_blocked:
        if_nothing += " — экзамен блокирующий"
    lines.append(if_nothing)
    if report.seminar_group is None:
        lines.extend(
            [
                "",
                "Семинарская группа не указана, посещаемость считаю только по лекциям. "
                "Напиши преподавателю",
            ]
        )
    for component in report.components:
        lines.append("")
        weight_pct = int(round_half_up(component.weight * 100, 0))
        head = f"{component.title} · вес {weight_pct}%"
        score = format_score(component.score_now)
        lines.append(f"{head:<36}{score}")
        if component.key == "attendance":
            held = report.attendance_held
            lines.append(
                f"  {report.attendance_present} из {held} прошедших занятий, "
                f"пропусков {report.attendance_absent}"
            )
            continue
        if component.key == "exam":
            exam = course.assessment_by_code("exam")
            if exam.deadline_ts is not None:
                extra = f"сдать до {format_dt(exam.deadline_ts)}"
                defense = course.defense_ts_for(exam, report.seminar_group)
                if defense is not None:
                    extra += f", защита {format_short_date(defense)}"
                lines.append(f"  {extra}")
            lines.append(
                "  Ниже 4 баллов — неудовлетворительно за курс независимо от накопленной."
            )
            continue
        if len(component.items) == 1:
            item = component.items[0]
            reason = _item_reason(item, course, report.as_of_ts)
            if item.status is ItemStatus.GRADED and item.applied_score is not None:
                if reason:
                    lines.append(f"  {reason}")
            elif reason:
                lines.append(f"  {reason}")
            continue
        for item in component.items:
            mark = format_score(item.applied_score) if item.status is ItemStatus.GRADED else "—"
            reason = _item_reason(item, course, report.as_of_ts)
            pad = f"  {item.label}  {mark}"
            if reason:
                lines.append(f"{pad:<12}{reason}" if len(pad) < 12 else f"{pad}     {reason}")
            else:
                lines.append(pad)
    return "\n".join(lines)


def format_attendance_list(
    lessons: list[CourseLesson],
    marks: dict[str, str],
    present: int,
    absent: int,
    excused: int,
    percent: float | None,
    score: float | None,
) -> str:
    kind_label = {"lecture": "лекция", "seminar": "семинар"}
    status_label = {
        "present": "был",
        "absent": "не был",
        "excused": "уважительная",
    }
    lines: list[str] = []
    for lesson in lessons:
        status = marks.get(lesson.code)
        mark = status_label.get(status, "—") if status else "—"
        lines.append(
            f"{format_short_date(lesson.starts_ts)} {kind_label.get(lesson.kind, lesson.kind)} · "
            f"{lesson.title} · {mark}"
        )
    denom = present + absent
    percent_text = "—" if percent is None else f"{format_score(percent, 0)}%"
    score_text = "—" if score is None else format_score(score)
    lines.append("")
    lines.append(
        f"Посещаемость: {present} из {denom}, {percent_text}, {score_text} баллов."
    )
    if excused:
        lines.append(f"Уважительных пропусков: {excused} — в знаменатель не входят.")
    lines.append(
        "Опоздание больше 20 минут считается отсутствием. "
        "Подтверждённые учебным офисом пропуски убираются из знаменателя."
    )
    return "\n".join(lines)
