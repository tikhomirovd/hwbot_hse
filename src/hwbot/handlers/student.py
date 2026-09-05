from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.filters.callback_data import CallbackData

from hwbot.commands import setup_commands
from hwbot.handlers.filters import PlainText
from hwbot.config import Settings, is_admin
from hwbot.db import Database
from hwbot.errors import (
    AlreadyBoundError,
    CourseError,
    DeadlineClosedError,
    HomeworkNotFoundError,
    StudentTakenError,
)
from hwbot.course import DEFAULT_COURSE_PATH, load_course
from hwbot.formatting import (
    format_attendance_list,
    format_grade_report,
    format_homework_card,
    format_hw_list,
    format_late_warning,
    format_profile,
    format_score,
    help_text,
)
from hwbot.grading import (
    attendance_percent,
    attendance_score,
    build_item,
    build_report,
    days_late,
    late_cap,
    student_lessons,
)
from hwbot.matching import match_students
from hwbot.ops import build_student_state
from hwbot.timeutil import now_ts

router = Router()


class RegisterStates(StatesGroup):
    waiting_identity = State()


class SubmitStates(StatesGroup):
    waiting_payload = State()


class ConfirmReg(CallbackData, prefix="reg"):
    action: str
    student_id: int


class PickHw(CallbackData, prefix="sub"):
    homework_id: int


def _callback_message(callback: CallbackQuery) -> Message | None:
    message = callback.message
    return message if isinstance(message, Message) else None


def _not_registered_text() -> str:
    return (
        "Привет. Напиши своё ФИО как в ведомости или корпоративную почту @edu.hse.ru.\n"
        "Например: Абрамова Анастасия Романовна"
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(help_text())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Ок, отменил.")


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
) -> None:
    await state.clear()
    if message.from_user is None:
        return
    student = await db.get_student_by_telegram(message.from_user.id)
    if student is not None:
        await message.answer(format_profile(student))
        return
    if is_admin(message.from_user.id, settings):
        if message.bot is not None:
            await setup_commands(message.bot, settings)
        await message.answer(
            "Ты админ. Студентом в списке тебя нет — это нормально.\n\n"
            "/newhw — новое ДЗ\n"
            "/status — кто сдал\n"
            "/export — выгрузка CSV\n"
            "/missing — кто не сдал"
        )
        return
    await state.set_state(RegisterStates.waiting_identity)
    await message.answer(_not_registered_text())


@router.message(StateFilter(RegisterStates.waiting_identity), F.text, PlainText())
async def register_identity(message: Message, state: FSMContext, db: Database) -> None:
    if message.text is None:
        return
    students = await db.list_students()
    result = match_students(message.text, students)
    unique = result.unique
    if unique is not None:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Да, это я",
                        callback_data=ConfirmReg(
                            action="yes", student_id=unique.id
                        ).pack(),
                    ),
                    InlineKeyboardButton(
                        text="Нет",
                        callback_data=ConfirmReg(action="no", student_id=unique.id).pack(),
                    ),
                ]
            ]
        )
        await message.answer(
            f"Это ты? {unique.full_name}, {unique.group_code}",
            reply_markup=keyboard,
        )
        return
    if result.students:
        lines = ["Нашёл несколько вариантов, напиши ФИО точнее:"]
        for student in result.students:
            lines.append(f"— {student.full_name}, {student.group_code}")
        await message.answer("\n".join(lines))
        return
    await message.answer(
        "В списке курса такого нет. Проверь ФИО или почту как в ведомости. "
        "Если всё верно — напиши преподу."
    )


@router.callback_query(ConfirmReg.filter())
async def confirm_registration(
    callback: CallbackQuery,
    callback_data: ConfirmReg,
    state: FSMContext,
    db: Database,
) -> None:
    message = _callback_message(callback)
    if message is None:
        return
    if callback_data.action != "yes":
        await state.set_state(RegisterStates.waiting_identity)
        await message.edit_text("Ок. Напиши ФИО или почту ещё раз.")
        await callback.answer()
        return
    username = callback.from_user.username
    try:
        student = await db.bind_telegram(
            callback_data.student_id,
            callback.from_user.id,
            username,
        )
    except AlreadyBoundError:
        await message.edit_text(
            "Этот Telegram уже привязан к другому человеку. Напиши преподу."
        )
        await callback.answer()
        return
    except StudentTakenError:
        await message.edit_text(
            "Этот человек уже зарегистрирован с другого аккаунта. Напиши преподу."
        )
        await callback.answer()
        return
    await state.clear()
    await message.edit_text(format_profile(student))
    await callback.answer("Готово")


@router.message(Command("hw"))
async def cmd_hw(message: Message, db: Database) -> None:
    if message.from_user is None:
        return
    student = await db.get_student_by_telegram(message.from_user.id)
    if student is None:
        await message.answer("Сначала зарегистрируйся: /start")
        return
    now = now_ts()
    assessments = await db.homeworks_for_group(student.group_code)
    cards: list[str] = []
    for assessment in assessments:
        submission = await db.latest_submission(student.id, assessment.id)
        cards.append(format_homework_card(assessment, submission, now))
    await message.answer(format_hw_list(cards))


@router.message(Command("mysubmissions"))
async def cmd_mysubmissions(message: Message, db: Database) -> None:
    if message.from_user is None:
        return
    student = await db.get_student_by_telegram(message.from_user.id)
    if student is None:
        await message.answer("Сначала зарегистрируйся: /start")
        return
    items = await db.list_student_submissions(student.id)
    if not items:
        await message.answer("Ты ещё ничего не сдавал.")
        return
    now = now_ts()
    course = load_course(DEFAULT_COURSE_PATH)
    known = {entry.code: entry for entry in course.assessments}
    cards: list[str] = []
    for assessment, submission in items:
        card = format_homework_card(assessment, submission, now)
        grade = await db.get_grade(student.id, assessment.id)
        if grade is not None:
            course_item = known.get(assessment.code)
            if course_item is not None:
                item = build_item(
                    course_item, course, now, submission.submitted_at, grade.score
                )
                card += f"\nБалл: {format_score(item.applied_score)}"
                if item.days_late > 0 and item.cap is not None:
                    card += (
                        f" (сдано с опозданием на {item.days_late} дн., "
                        f"потолок {format_score(item.cap)})"
                    )
            else:
                card += f"\nБалл: {format_score(grade.score)}"
        cards.append(card)
    await message.answer(format_hw_list(cards))


@router.message(Command("submit"))
async def cmd_submit(message: Message, state: FSMContext, db: Database) -> None:
    if message.from_user is None:
        return
    student = await db.get_student_by_telegram(message.from_user.id)
    if student is None:
        await message.answer("Сначала зарегистрируйся: /start")
        return
    now = now_ts()
    open_homeworks = [
        item
        for item in await db.homeworks_for_group(student.group_code)
        if item.accept_until_ts is None or item.accept_until_ts >= now
    ]
    if not open_homeworks:
        await message.answer(
            "Сейчас нет открытых работ. Если приём закрыт — будет 0."
        )
        return
    buttons = [
        [
            InlineKeyboardButton(
                text=f"#{item.id} {item.label}",
                callback_data=PickHw(homework_id=item.id).pack(),
            )
        ]
        for item in open_homeworks
    ]
    await message.answer(
        "Какое ДЗ сдаёшь?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(PickHw.filter())
async def pick_homework(
    callback: CallbackQuery,
    callback_data: PickHw,
    state: FSMContext,
    db: Database,
) -> None:
    message = _callback_message(callback)
    if message is None:
        return
    student = await db.get_student_by_telegram(callback.from_user.id)
    if student is None:
        await callback.answer("Сначала /start", show_alert=True)
        return
    homework = await db.get_homework(callback_data.homework_id)
    if homework is None:
        await callback.answer("Задание не найдено", show_alert=True)
        return
    now = now_ts()
    if homework.accept_until_ts is not None and homework.accept_until_ts < now:
        await message.edit_text(
            f"Приём по «{homework.title}» уже закрыт. Оценка 0."
        )
        await callback.answer()
        return
    await state.set_state(SubmitStates.waiting_payload)
    await state.update_data(homework_id=homework.id)
    prompt = (
        f"Ок, «{homework.title}». Пришли ссылку на GitHub или любой текст сдачи.\n"
        "Отмена: /cancel"
    )
    if homework.deadline_ts is not None and now > homework.deadline_ts:
        course = load_course(DEFAULT_COURSE_PATH)
        try:
            rule = course.late_rule_named(homework.late_rule)
        except CourseError:
            rule = course.late_rule_named("homework")
        late_days = days_late(now, homework.deadline_ts)
        cap = late_cap(rule, late_days)
        prompt = f"{format_late_warning(late_days, cap)}\n\n{prompt}"
    await message.edit_text(prompt)
    await callback.answer()


@router.message(StateFilter(SubmitStates.waiting_payload), F.text, PlainText())
async def receive_submission(
    message: Message,
    state: FSMContext,
    db: Database,
) -> None:
    if message.from_user is None or message.text is None:
        return
    student = await db.get_student_by_telegram(message.from_user.id)
    if student is None:
        await state.clear()
        await message.answer("Сначала зарегистрируйся: /start")
        return
    data = await state.get_data()
    homework_id = data.get("homework_id")
    if not isinstance(homework_id, int):
        await state.clear()
        await message.answer("Что-то сломалось, нажми /submit ещё раз.")
        return
    try:
        submission = await db.add_submission(student.id, homework_id, message.text)
    except DeadlineClosedError:
        await state.clear()
        await message.answer("Приём уже закрыт. Оценка 0.")
        return
    except HomeworkNotFoundError as exc:
        await state.clear()
        await message.answer(str(exc))
        return
    except ValueError:
        await message.answer("Пустое сообщение не принимаю. Пришли ссылку или текст.")
        return
    homework = await db.get_homework(homework_id)
    await state.clear()
    title = homework.title if homework is not None else "ДЗ"
    await message.answer(f"Принял «{title}».\n{submission.payload}")


@router.message(Command("grade"))
async def cmd_grade(message: Message, db: Database) -> None:
    if message.from_user is None:
        return
    student = await db.get_student_by_telegram(message.from_user.id)
    if student is None:
        await message.answer("Сначала зарегистрируйся: /start")
        return
    course = load_course(DEFAULT_COURSE_PATH)
    state = await build_student_state(db, student, course)
    report = build_report(state, course, now_ts())
    await message.answer(format_grade_report(report, course))


@router.message(Command("attendance"))
async def cmd_attendance(message: Message, db: Database) -> None:
    if message.from_user is None:
        return
    student = await db.get_student_by_telegram(message.from_user.id)
    if student is None:
        await message.answer("Сначала зарегистрируйся: /start")
        return
    course = load_course(DEFAULT_COURSE_PATH)
    state = await build_student_state(db, student, course)
    lessons = list(student_lessons(course, student.seminar_group))
    present = 0
    absent = 0
    excused = 0
    for lesson in lessons:
        status = state.attendance.get(lesson.code)
        if status == "present":
            present += 1
        elif status == "absent":
            absent += 1
        elif status == "excused":
            excused += 1
    percent = attendance_percent(present, absent, excused)
    score = attendance_score(present, absent, excused, course.attendance_scale)
    text = format_attendance_list(
        lessons, dict(state.attendance), present, absent, excused, percent, score
    )
    if student.seminar_group is None:
        text = (
            "Семинарская группа не указана, посещаемость считаю только по лекциям. "
            "Напиши преподавателю\n\n"
            + text
        )
    await message.answer(text)


@router.message(StateFilter(RegisterStates.waiting_identity))
async def register_need_text(message: Message) -> None:
    await message.answer("Нужен текст: ФИО или почта.")


@router.message(StateFilter(SubmitStates.waiting_payload))
async def submit_need_text(message: Message) -> None:
    await message.answer("Пришли текстом ссылку или описание. Отмена: /cancel")
