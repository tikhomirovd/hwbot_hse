from __future__ import annotations

from hwbot.availability import is_accept_open, is_current, is_upcoming, looks_like_submission
from hwbot.course import Course, LateRule, Lesson as CourseLesson
from hwbot.grading import GradeReport, ItemResult, ItemStatus, late_cap, round_half_up
from hwbot.models import Assessment, Student, Submission
from hwbot.telegramutil import display_payload, escape_html
from hwbot.timeutil import (
    format_human_datetime,
    format_human_day,
    format_human_dt,
    is_same_calendar_day,
    remaining_seconds,
    weekday_prepositional,
    zone,
)
from datetime import datetime

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


def format_short_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, zone()).strftime("%d.%m")


def format_day_month(ts: int) -> str:
    moment = datetime.fromtimestamp(ts, zone())
    return f"{moment.day} {MONTHS_GENITIVE[moment.month - 1]}"


def format_score(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "—"
    rounded = round_half_up(value, digits)
    if digits == 0 or rounded == int(rounded):
        if digits == 0:
            return str(int(rounded))
    text = f"{rounded:.{digits}f}"
    return text.replace(".", ",")


def format_cap(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return format_score(value)


def work_name(assessment: Assessment) -> str:
    return assessment.label or assessment.title


def work_heading(assessment: Assessment) -> str:
    label = escape_html(work_name(assessment))
    title = escape_html(assessment.title)
    if assessment.label and assessment.label != assessment.title:
        return f"{label} · {title}"
    return label


def given_name(full_name: str) -> str:
    parts = full_name.split()
    if len(parts) >= 2:
        return parts[1]
    return parts[0]


def _days_word(days: int) -> str:
    if days % 10 == 1 and days % 100 != 11:
        return "день"
    if days % 10 in {2, 3, 4} and days % 100 not in {12, 13, 14}:
        return "дня"
    return "дней"


def _hours_word(hours: int) -> str:
    if hours % 10 == 1 and hours % 100 != 11:
        return "час"
    if hours % 10 in {2, 3, 4} and hours % 100 not in {12, 13, 14}:
        return "часа"
    return "часов"


def _lessons_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "занятие"
    if count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
        return "занятия"
    return "занятий"


def seminar_line(student: Student, course: Course) -> str | None:
    if student.seminar_group is None:
        return None
    for lesson in course.lessons:
        if lesson.kind == "seminar" and lesson.seminar_group == student.seminar_group:
            return f"Семинары по {weekday_prepositional(lesson.starts_ts)}."
    return None


def welcome_unregistered() -> str:
    return (
        "👋 Привет! Это бот курса «Программирование на Python для бизнес-аналитики».\n\n"
        "Здесь ты сдаёшь работы и в любой момент видишь свою оценку — ровно ту же, "
        "что стоит у преподавателя.\n\n"
        "Давай опознаемся. Напиши своё ФИО как в ведомости или корпоративную почту.\n\n"
        "Например:\n"
        "Абрамова Анастасия Романовна\n"
        "или arabramova_1@edu.hse.ru"
    )


def confirm_one_student(student: Student) -> str:
    return (
        "Проверим, что я не перепутал.\n\n"
        f"<b>{escape_html(student.full_name)}</b>\n"
        f"{escape_html(student.group_code)}\n\n"
        "Это ты?"
    )


def confirm_many_students() -> str:
    return "Нашёл несколько похожих. Кто из них ты?"


def register_not_found() -> str:
    return (
        "🤔 Не нахожу такого в списке курса.\n\n"
        "Проверь, что ФИО написано полностью и как в ведомости. Ещё вернее — "
        "пришли корпоративную почту @edu.hse.ru, по ней я нахожу точно.\n\n"
        "Если всё верно, а я упрямлюсь — напиши преподавателю, поправим руками."
    )


def register_need_text() -> str:
    return "Нужен текст: ФИО или почта."


def register_try_again() -> str:
    return "Ок. Напиши ФИО или почту ещё раз."


def already_bound_text() -> str:
    return (
        "Этот Telegram уже привязан к другому человеку из списка.\n\n"
        "Если это ошибка — напиши преподавателю, он отвяжет за минуту."
    )


def student_taken_text() -> str:
    return (
        "Под этим именем уже кто-то зарегистрировался — возможно, кто-то ошибся при выборе.\n\n"
        "Напиши преподавателю, разберёмся и вернём тебе твою запись."
    )


def register_done(student: Student) -> str:
    name = escape_html(given_name(student.full_name))
    return (
        f"✅ Записал, {name}.\n\n"
        "Что тут есть:\n"
        "📌 /hw — что сдавать прямо сейчас\n"
        "📤 /submit — сдать работу\n"
        "📊 /grade — оценка и из чего она сложилась\n"
        "🗓 /attendance — посещаемость\n"
        "❓ /help — если что-то непонятно\n\n"
        "Кстати, ссылку на работу можно просто прислать сюда сообщением — я пойму."
    )


def format_profile(student: Student, course: Course | None = None) -> str:
    lines = [
        f"Ты <b>{escape_html(student.full_name)}</b>, {escape_html(student.group_code)}."
    ]
    if course is not None:
        extra = seminar_line(student, course)
        if extra is not None:
            lines.append(extra)
    lines.extend(
        [
            "",
            "📌 /hw — что сдавать сейчас",
            "📤 /submit — сдать работу",
            "📎 /mysubmissions — что уже сдано",
            "📊 /grade — оценка",
            "🗓 /attendance — посещаемость",
            "❓ /help",
        ]
    )
    return "\n".join(lines)


def help_text() -> str:
    return (
        "❓ <b>Как здесь всё устроено</b>\n\n"
        "📌 /hw — что сдавать прямо сейчас\n"
        "📤 /submit — сдать работу\n"
        "📎 /mysubmissions — что уже сдано и с каким баллом\n"
        "📊 /grade — оценка и из чего она складывается\n"
        "🗓 /attendance — посещаемость\n"
        "🔄 /cancel — выйти из любого диалога\n\n"
        "<b>Про сроки</b>\n\n"
        "Дедлайн — это не «всё пропало». После него приём открыт ещё неделю, "
        "но каждые начатые сутки опускают потолок балла на 1. Ниже 4 в эту неделю "
        "потолок не падает.\n\n"
        "А вот когда неделя кончится — приём закроется совсем, и это уже 0. "
        "Разница между «сдал на четвёртый день» и «не сдал» большая, так что "
        "присылай даже поздно.\n\n"
        "<b>Про пересдачу</b>\n\n"
        "Прислать новую версию можно, пока открыт приём: считаю последнюю. "
        "Но если первая была в срок, а новая — уже после дедлайна, потолок "
        "посчитается по новой. Я предупрежу перед тем, как принять.\n\n"
        "<b>Про оценку</b>\n\n"
        "Я показываю ровно те же числа, что стоят у преподавателя, и объясняю каждое. "
        "Видишь что-то странное — напиши, разберёмся.\n\n"
        "Ссылку на работу можно просто прислать сюда сообщением, без команд."
    )


def not_registered_text() -> str:
    return "Сначала опознаемся — /start. Это одно сообщение с твоим ФИО."


def cancel_text() -> str:
    return "Ок, отменил. Если что — /hw"


def lost_thread_text() -> str:
    return "Кажется, я потерял нить. Начнём заново: /submit"


def crashed_text() -> str:
    return (
        "Что-то у меня сломалось. Попробуй ещё раз через минуту, "
        "а если повторится — напиши преподавателю, это моя вина, а не твоя."
    )


def empty_payload_text() -> str:
    return "Пустое сообщение принять не могу. Нужна ссылка или текст."


def file_not_accepted_text() -> str:
    return (
        "Файлы я принимать не умею — залей в репозиторий и пришли ссылку. "
        "Или просто текстом, если работа не в гите."
    )


def accept_closed_text(assessment: Assessment) -> str:
    name = escape_html(work_name(assessment))
    return (
        f"Приём по {name} закрыт, за неё стоит 0. "
        "Открыть обратно я не могу — если есть уважительная причина, напиши преподавателю."
    )


def not_issued_text(assessment: Assessment) -> str:
    name = escape_html(work_name(assessment))
    if assessment.issued_at is None:
        return f"{name} ещё не выдана. Напомню, когда откроется."
    return (
        f"{name} ещё не выдана — она появится {format_human_day(assessment.issued_at)}. "
        "Напомню, когда откроется."
    )


def fallback_generic() -> str:
    return (
        "Я тут в основном про сдачи и баллы 🙂\n\n"
        "📌 /hw — что сдавать\n"
        "📊 /grade — оценка\n"
        "❓ /help — всё остальное\n\n"
        "Если вопрос живому человеку — напиши преподавателю напрямую."
    )


def stub_one_work(assessment: Assessment, payload: str) -> str:
    return (
        f"Похоже на сдачу. Принять как <b>{escape_html(work_name(assessment))}</b>?\n\n"
        f"{display_payload(payload)}"
    )


def stub_many_works() -> str:
    return "Похоже на сдачу. По какой работе?"


def fallback_reply(text: str, open_works: list[Assessment]) -> str:
    if not looks_like_submission(text, has_open_work=bool(open_works)):
        return fallback_generic()
    if not open_works:
        return fallback_generic()
    if len(open_works) == 1:
        return stub_one_work(open_works[0], text)
    return stub_many_works()


def submit_prompt(assessment: Assessment) -> str:
    return (
        f"📤 Сдаём <b>{work_heading(assessment)}</b>\n\n"
        "Пришли ссылку на репозиторий одним сообщением. "
        "Если работа не в гите — просто текстом.\n\n"
        "Передумал — /cancel"
    )


def submit_choose() -> str:
    return "Что сдаёшь?"


def submit_need_text() -> str:
    return "Пришли текстом ссылку или описание. Отмена: /cancel"


def late_submit_warning(
    assessment: Assessment,
    days: int,
    cap: float,
    rule: LateRule,
) -> str:
    name = escape_html(work_name(assessment))
    accept = assessment.accept_until_ts
    accept_line = ""
    if accept is not None:
        accept_line = (
            f"Приём закроется {format_human_dt(accept)}. После этого — 0.\n\n"
        )
    floor_line = ""
    if rule.floor > 0:
        floor_line = (
            f" Ниже {format_cap(rule.floor)} потолок в течение недели не опустится."
        )
    return (
        f"⚠️ Дедлайн по {name} прошёл {days} {_days_word(days)} назад.\n\n"
        f"Работу приму, но выше <b>{format_cap(cap)} из 10</b> поставить уже не смогу "
        f"— и каждые следующие сутки это ещё минус балл.{floor_line}\n\n"
        f"{accept_line}"
        "Присылай ссылку, если готов. Или /cancel."
    )


def resubmit_confirm(
    assessment: Assessment,
    previous: Submission,
    new_cap: float,
) -> str:
    name = escape_html(work_name(assessment))
    when = format_human_day(previous.submitted_at)
    return (
        f"Ты уже сдал {name} <b>в срок</b>, {when}.\n\n"
        f"Новая версия считается по времени отправки — потолок станет "
        f"<b>{format_cap(new_cap)} из 10</b>. Прежнюю сдачу это заменит.\n\n"
        "Точно присылать?"
    )


def accepted_on_time(
    assessment: Assessment,
    submission: Submission,
) -> str:
    name = escape_html(work_name(assessment))
    accept = assessment.accept_until_ts
    until = ""
    if accept is not None:
        until = f" До {format_human_day(accept)} можно прислать новую версию."
    return (
        f"✅ Принял {name}.\n\n"
        f"{display_payload(submission.payload)}\n"
        f"Сдано {format_human_datetime(submission.submitted_at)} — в срок.\n\n"
        f"Проверю и выставлю балл, он появится в /grade.{until}"
    )


def accepted_late(
    assessment: Assessment,
    submission: Submission,
    cap: float,
    days: int,
) -> str:
    name = escape_html(work_name(assessment))
    return (
        f"✅ Принял {name}.\n\n"
        f"{display_payload(submission.payload)}\n"
        f"Сдано {format_human_datetime(submission.submitted_at)} — "
        f"на {days} {_days_word(days)} позже дедлайна, потолок "
        f"<b>{format_cap(cap)} из 10</b>.\n\n"
        "Проверю и выставлю балл. Он появится в /grade."
    )


def accepted_update(assessment: Assessment, submission: Submission) -> str:
    name = escape_html(work_name(assessment))
    return (
        f"✅ Обновил {name}. Считаю последнюю версию.\n\n"
        f"{display_payload(submission.payload)}"
    )


def _remaining_line(deadline_ts: int, now: int) -> str:
    left = remaining_seconds(deadline_ts, now)
    until = format_human_dt(deadline_ts)
    if is_same_calendar_day(deadline_ts, now):
        until = f"сегодня, {datetime.fromtimestamp(deadline_ts, zone()).strftime('%H:%M')}"
    if left <= 0:
        return f"⏳ Дедлайн: {format_human_dt(deadline_ts)}"
    if left < 86400:
        hours = max(1, left // 3600)
        return f"⏳ Осталось {hours} {_hours_word(hours)} — до {until}"
    days = left // 86400
    return f"⏳ Осталось {days} {_days_word(days)} — до {until}"


def _late_open_lines(assessment: Assessment, days: int, cap: float, rule: LateRule) -> list[str]:
    accept = assessment.accept_until_ts
    accept_text = format_human_dt(accept) if accept is not None else "закрытия"
    floor = ""
    if rule.floor > 0:
        floor = f", каждые сутки — минус один. Ниже {format_cap(rule.floor)} в эту неделю не упадёт."
    else:
        floor = ", каждые сутки — минус один."
    return [
        f"⚠️ Дедлайн прошёл {days} {_days_word(days)} назад. Приём открыт до {accept_text}",
        f"Потолок балла сейчас <b>{format_cap(cap)} из 10</b>{floor}",
    ]


def _grade_line(item: ItemResult) -> str:
    if item.status is not ItemStatus.GRADED or item.applied_score is None:
        return "Балл пока не выставлен — проверяю."
    line = f"Балл: <b>{format_score(item.applied_score, 0 if item.applied_score == int(item.applied_score) else 1)}</b> из 10"
    if (
        item.days_late > 0
        and item.raw_score is not None
        and item.raw_score != item.applied_score
    ):
        raw = format_score(item.raw_score, 0 if item.raw_score == int(item.raw_score) else 1)
        line += (
            f" — поставлено {raw}, срезано потолком за {item.days_late} "
            f"{_days_word(item.days_late)} просрочки"
        )
    return line


def format_homework_card(
    assessment: Assessment,
    submission: Submission | None,
    now: int,
    *,
    course: Course | None = None,
    item: ItemResult | None = None,
) -> str:
    lines = [f"<b>{work_heading(assessment)}</b>"]
    rule: LateRule | None = None
    if course is not None:
        try:
            rule = course.late_rule_named(assessment.late_rule)
        except Exception:
            rule = None
    deadline = assessment.deadline_ts
    if submission is None:
        if deadline is not None and now > deadline and is_accept_open(assessment, now):
            days = max(1, (now - deadline + 86399) // 86400)
            if course is not None:
                from hwbot.grading import days_late as _days_late

                days = _days_late(now, deadline)
            cap = 9.0
            if rule is not None:
                cap = late_cap(rule, days)
            lines.extend(_late_open_lines(assessment, days, cap, rule or LateRule("homework", 1, 4, 7, None)))
        elif deadline is not None:
            lines.append(_remaining_line(deadline, now))
        lines.append("Пока не сдано")
        if assessment.body.strip():
            lines.append("")
            lines.append(escape_html(assessment.body.strip()))
        return "\n".join(lines)

    on_time = deadline is None or submission.submitted_at <= deadline
    if on_time:
        lines.append(
            f"✅ Сдано {format_human_datetime(submission.submitted_at)}, в срок"
        )
    else:
        from hwbot.grading import days_late as _days_late

        days = _days_late(submission.submitted_at, deadline or submission.submitted_at)
        cap_text = ""
        if rule is not None:
            cap_text = f" — потолок {format_cap(late_cap(rule, days))} из 10"
        lines.append(
            f"✅ Сдано {format_human_datetime(submission.submitted_at)}, "
            f"на {days} {_days_word(days)} позже дедлайна{cap_text}"
        )
    lines.append(f"Твоя ссылка: {display_payload(submission.payload)}")
    if item is not None:
        lines.append(_grade_line(item))
    else:
        lines.append("Балл пока не выставлен — проверяю.")
    return "\n".join(lines)


def format_soon_block(upcoming: list[Assessment]) -> str:
    lines = ["🗓 <b>Скоро</b>"]
    for item in upcoming:
        name = escape_html(work_name(item))
        when = (
            format_human_day(item.issued_at)
            if item.issued_at is not None
            else "скоро"
        )
        extra = ""
        if item.code == "exam":
            extra = "командный проект, "
        lines.append(f"{name} — {extra}выдадим {when}".replace("— командный проект, выдадим", "— командный проект, выдадим"))
        if item.code == "exam" and item.issued_at is not None:
            lines[-1] = f"{name} — командный проект, выдадим {when}"
    return "\n".join(lines)


def format_hw_empty_soon(upcoming: list[Assessment]) -> str:
    if not upcoming:
        return (
            "Всё сдано, приём закрыт по всем работам.\n\n"
            "📊 Итог — в /grade"
        )
    first = upcoming[0]
    name = escape_html(work_name(first))
    when = (
        format_human_day(first.issued_at)
        if first.issued_at is not None
        else "скоро"
    )
    return (
        "🎉 Сейчас сдавать нечего.\n\n"
        f"Ближайшая работа — {name}, выдадим {when}. Напомню, когда появится."
    )


def format_mysubmissions(
    items: list[tuple[Assessment, Submission, ItemResult | None]],
) -> str:
    if not items:
        return "Пока ничего не сдано.\n\n📌 Что сдавать — /hw"
    blocks = ["📎 <b>Что ты уже сдал</b>"]
    for assessment, submission, item in items:
        heading = (
            f"<b>{escape_html(work_name(assessment))}</b> · "
            f"{escape_html(assessment.title)}"
            if assessment.label and assessment.label != assessment.title
            else f"<b>{escape_html(work_name(assessment))}</b>"
        )
        deadline = assessment.deadline_ts
        on_time = deadline is None or submission.submitted_at <= deadline
        if on_time:
            status = f"Сдано {format_human_day(submission.submitted_at)}, в срок"
        else:
            from hwbot.grading import days_late as _days_late

            days = _days_late(submission.submitted_at, deadline or 0)
            status = (
                f"Сдано {format_human_day(submission.submitted_at)}, "
                f"на {days} {_days_word(days)} позже дедлайна"
            )
        lines = [
            heading,
            status,
            display_payload(submission.payload),
        ]
        if item is not None and item.status is ItemStatus.GRADED and item.applied_score is not None:
            score = format_score(
                item.applied_score,
                0 if item.applied_score == int(item.applied_score) else 1,
            )
            lines.append(f"Балл: <b>{score}</b> из 10")
        else:
            lines.append("Проверяю, балл будет в /grade")
        blocks.append("\n".join(lines))
    return "\n\n—————\n\n".join(blocks)


def _item_reason(item: ItemResult, course: Course, now: int) -> str:
    assessment = course.assessment_by_code(item.code)
    if item.status is ItemStatus.GRADED:
        if item.days_late > 0 and item.raw_score is not None and item.applied_score is not None:
            if item.raw_score != item.applied_score:
                return (
                    f"поставлено {format_score(item.raw_score, 0 if item.raw_score == int(item.raw_score) else 1)}, "
                    f"срезано за {item.days_late} {_days_word(item.days_late)} просрочки"
                )
            return (
                f"сдано с опозданием на {item.days_late} {_days_word(item.days_late)}, "
                f"потолок {format_score(item.cap)}"
            )
        return ""
    if item.status is ItemStatus.AWAITING:
        if assessment.submit_via_bot:
            return "сдано, ждёт проверки"
        return "ждёт проверки"
    if item.status is ItemStatus.MISSED:
        return "не сдано, приём закрыт"
    if item.status is ItemStatus.OPEN:
        if assessment.deadline_ts is not None and now > assessment.deadline_ts:
            close = assessment.accept_until_ts or assessment.deadline_ts
            return f"принимается до {format_short_date(close)}, 23:59"
        return "приём открыт"
    if assessment.submit_via_bot and assessment.issued_at is not None:
        return f"выдадим {format_short_date(assessment.issued_at)}"
    if assessment.graded_on_ts is not None:
        return f"пишем {format_short_date(assessment.graded_on_ts)}"
    return "ещё не выдано"


def format_grade_breakdown(report: GradeReport, course: Course) -> str:
    lines: list[str] = []
    for component in report.components:
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
                extra = f"сдать до {format_short_date(exam.deadline_ts)}"
                defense = course.defense_ts_for(exam, report.seminar_group)
                if defense is not None:
                    extra += f", защита {format_short_date(defense)}"
                lines.append(f"  {extra}")
            continue
        if len(component.items) == 1:
            item = component.items[0]
            reason = _item_reason(item, course, report.as_of_ts)
            if reason:
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


def format_grade_report(report: GradeReport, course: Course) -> str:
    if report.heading_to is None and report.in_pocket is None:
        return (
            "📊 Считать пока нечего: ни одного балла и ни одной отметки о посещении.\n\n"
            "Первые числа появятся после первой проверочной работы и первой отметки "
            "о посещении на паре. Загляни сюда после 19 сентября."
        )
    lines = [
        f"📊 <b>Твоя оценка на {format_day_month(report.as_of_ts)}</b>",
        "",
        f"<b>Идёшь на {format_score(report.heading_to)} из 10</b>",
        "Так выглядит курс, если дальше пойдёт как идёт: считаю только по проверенному.",
        "",
        f"<b>В кармане {format_score(report.in_pocket)} из 7</b>",
        "Это накопленная прямо сейчас. Непроверенное считаю нулём, поэтому в начале "
        "семестра число маленькое — это нормально.",
        "",
        f"<b>Если остановиться совсем: {format_score(report.if_nothing)}</b>",
        "Ничего больше не сдавать и не ходить.",
    ]
    if report.exam_blocked:
        lines.extend(
            [
                "",
                "⚠️ Экзамен блокирующий: ниже 4 — неудовлетворительно за курс, "
                "какой бы ни была накопленная.",
            ]
        )
    breakdown = format_grade_breakdown(report, course)
    lines.extend(["", f"<pre>{escape_html(breakdown)}</pre>"])
    return "\n".join(lines)


def format_attendance_summary(
    *,
    present: int,
    absent: int,
    excused: int,
    held: int,
    percent: float | None,
    score: float | None,
    remaining: int,
    forecast_if_attend: float | None,
    absences: list[CourseLesson],
    seminar_unknown: bool,
    no_held: bool,
) -> str:
    if no_held:
        return (
            "🗓 <b>Посещаемость</b>\n\n"
            "Занятий ещё не было — считать нечего. Первая отметка о посещении на первой паре."
        )
    score_text = "—" if score is None else f"<b>{format_score(score, 0 if score == int(score) else 1)} баллов</b>"
    percent_text = "—" if percent is None else f"{format_score(percent, 0)}%"
    lines = [
        "🗓 <b>Посещаемость</b>",
        "",
        f"Был на {present} из {held} прошедших занятий — {percent_text}, это {score_text} из 10.",
    ]
    if absences:
        lines.append("")
        lines.append("Пропущено:")
        kind_label = {"lecture": "лекция", "seminar": "семинар"}
        for lesson in absences:
            lines.append(
                f"{format_short_date(lesson.starts_ts)} · "
                f"{kind_label.get(lesson.kind, lesson.kind)} · "
                f"{escape_html(lesson.title)}"
            )
    if remaining > 0:
        lines.append("")
        forecast = (
            f" Если не пропускать — выйдешь на {format_score(forecast_if_attend, 0)}."
            if forecast_if_attend is not None
            else ""
        )
        lines.append(
            f"Впереди ещё {remaining} {_lessons_word(remaining)}.{forecast}"
        )
    lines.extend(
        [
            "",
            "Уважительные пропуски, подтверждённые учебным офисом, из знаменателя убираются. "
            "Опоздание больше 20 минут считается отсутствием.",
        ]
    )
    if seminar_unknown:
        lines.extend(
            [
                "",
                "Пока считаю только лекции: семинарская группа появится после твоего первого семинара.",
            ]
        )
    _ = excused
    return "\n".join(lines)


def format_attendance_full_list(
    lessons: list[CourseLesson],
    marks: dict[str, str],
    held_codes: frozenset[str],
) -> str:
    kind_label = {"lecture": "лекция", "seminar": "семинар"}
    lines: list[str] = []
    for lesson in lessons:
        if lesson.code in marks:
            status = {
                "present": "был",
                "absent": "не был",
                "excused": "уважительная",
            }.get(marks[lesson.code], "не был")
        elif lesson.code in held_codes:
            status = "не был"
        else:
            status = "ещё впереди"
        lines.append(
            f"{format_short_date(lesson.starts_ts)} · "
            f"{kind_label.get(lesson.kind, lesson.kind)} · "
            f"{escape_html(lesson.title)} · {status}"
        )
    return "\n".join(lines)


def new_homework_announcement(assessment: Assessment) -> str:
    deadline = (
        format_human_dt(assessment.deadline_ts)
        if assessment.deadline_ts is not None
        else "дедлайна"
    )
    body = escape_html(assessment.body.strip()) if assessment.body.strip() else ""
    body_block = f"\n\n{body}" if body else ""
    return (
        f"📌 Новая работа: <b>{work_heading(assessment)}</b>\n\n"
        f"⏳ Сдать до {deadline} — это неделя.{body_block}\n\n"
        "Работа одна для обеих групп. Подробности — на семинаре.\n\n"
        "📤 /submit"
    )


def admin_home_text() -> str:
    return (
        "Ты админ. Студентом в списке тебя нет — это нормально.\n\n"
        "/status — кто сдал\n"
        "/export — выгрузка CSV\n"
        "/missing — кто не сдал"
    )


def format_late_warning(days: int, cap: float, rule_floor: float | None = None) -> str:
    extra = ""
    if rule_floor is not None and rule_floor > 0:
        extra = f" Ниже {format_cap(rule_floor)} в эту неделю не упадёт."
    return (
        f"Дедлайн уже прошёл. Сейчас потолок {format_score(cap)} "
        f"за {days} {_days_word(days)} просрочки.{extra}"
    )


def homework_status_for_student(
    assessment: Assessment,
    submission: Submission | None,
    now: int,
) -> str:
    if submission is not None:
        if (
            assessment.deadline_ts is not None
            and submission.submitted_at > assessment.deadline_ts
        ):
            return "сдано с опозданием"
        return "сдано"
    if is_current(assessment, now):
        if assessment.deadline_ts is not None and now > assessment.deadline_ts:
            return "просрочено"
        return "не сдано"
    if is_upcoming(assessment, now):
        return "ещё не выдано"
    return "приём закрыт · 0"
