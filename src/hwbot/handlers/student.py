from __future__ import annotations

import logging
from collections.abc import Sequence

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from hwbot.availability import (
    current_assessments,
    looks_like_submission,
    upcoming_assessments,
    would_add_penalty,
)
from hwbot.commands import setup_commands
from hwbot.config import Settings, is_admin
from hwbot.course import DEFAULT_COURSE_PATH, Course, LateRule, load_course
from hwbot.db import Database
from hwbot.errors import (
    AlreadyBoundError,
    AlreadyInTeamError,
    CourseError,
    DeadlineClosedError,
    HomeworkNotFoundError,
    InviteGoneError,
    NotInTeamError,
    NotIssuedError,
    StudentTakenError,
    TeamFullError,
    TeamNotFoundError,
)
from hwbot.formatting import (
    accept_closed_text,
    accept_confirm_text,
    accepted_late,
    accepted_on_time,
    accepted_update,
    admin_home_text,
    already_bound_text,
    cancel_text,
    confirm_many_students,
    confirm_one_student,
    db_access_missing_text,
    db_access_text,
    empty_payload_text,
    fallback_generic,
    fallback_reply,
    file_not_accepted_text,
    format_attendance_full_list,
    format_attendance_summary,
    format_grade_report,
    format_homework_card,
    format_hw_empty_soon,
    format_mysubmissions,
    format_profile,
    format_people,
    format_soon_block,
    format_team_card,
    help_text,
    late_submit_warning,
    lost_thread_text,
    not_issued_text,
    not_registered_text,
    register_done,
    register_need_text,
    register_not_found,
    register_try_again,
    resubmit_confirm,
    short_name,
    student_taken_text,
    stub_many_works,
    stub_one_work,
    submit_button_text,
    submit_choose,
    submit_need_text,
    submit_prompt,
    team_cancelled_notice,
    team_declined_notice,
    team_dissolved_notice,
    team_invite_gone_notice,
    team_invite_text,
    team_leave_confirm_text,
    team_list_heading,
    team_member_joined_notice,
    team_member_left_notice,
    team_name_bad_text,
    team_name_prompt,
    team_no_room_text,
    team_renamed_notice,
    team_search_empty_text,
    team_search_heading,
    team_search_prompt,
    team_search_short_text,
    teams_locked_text,
    welcome_unregistered,
    work_name,
)
from hwbot.grading import (
    ItemResult,
    StudentState,
    attendance_percent,
    attendance_score,
    build_item,
    build_report,
    count_attendance,
    days_late,
    late_penalty,
    student_lessons,
)
from hwbot.handlers.filters import PlainText
from hwbot.matching import match_students, normalize_text, search_students
from hwbot.models import Assessment, Invite, Student, Submission, Team, clean_team_name
from hwbot.notify import notify_admins_submission, send_to_students
from hwbot.ops import build_student_state
from hwbot.telegramutil import answer_long
from hwbot.timeutil import now_ts

logger = logging.getLogger(__name__)

router = Router()
fallback_router = Router()


class RegisterStates(StatesGroup):
    waiting_identity = State()


class SubmitStates(StatesGroup):
    waiting_payload = State()
    confirming_resubmit = State()


class ConfirmReg(CallbackData, prefix="reg"):
    action: str
    student_id: int


class PickIdentity(CallbackData, prefix="who"):
    action: str
    student_id: int


class PickHw(CallbackData, prefix="sub"):
    homework_id: int


class OfferSubmit(CallbackData, prefix="off"):
    action: str
    homework_id: int


class ConfirmResub(CallbackData, prefix="rsb"):
    action: str
    homework_id: int


class ShowAttendance(CallbackData, prefix="att"):
    action: str


def _callback_message(callback: CallbackQuery) -> Message | None:
    message = callback.message
    return message if isinstance(message, Message) else None


def _course() -> Course:
    return load_course(DEFAULT_COURSE_PATH)


async def _submittable(db: Database) -> list[Assessment]:
    return await db.submittable_assessments()


def _open_now(assessments: list[Assessment], now: int) -> list[Assessment]:
    return current_assessments(assessments, now)


def _late_rule(course: Course, assessment: Assessment) -> LateRule:
    try:
        return course.late_rule_named(assessment.late_rule)
    except CourseError:
        return course.late_rule_named("homework")


def _submit_keyboard(assessments: list[Assessment]) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=work_name(item),
                callback_data=PickHw(homework_id=item.id).pack(),
            )
        ]
        for item in assessments
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _card_keyboard(
    assessment: Assessment, *, submitted: bool = False
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=submit_button_text(assessment, submitted=submitted),
                    callback_data=PickHw(homework_id=assessment.id).pack(),
                )
            ]
        ]
    )


def _accept_keyboard(assessment: Assessment) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, сдаю",
                    callback_data=OfferSubmit(
                        action="yes", homework_id=assessment.id
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Нет, я просто так",
                    callback_data=OfferSubmit(action="no", homework_id=0).pack(),
                ),
            ]
        ]
    )


async def _prompt_for_homework(
    message: Message,
    state: FSMContext,
    homework: Assessment,
    *,
    edit: bool = False,
) -> None:
    now = now_ts()
    await state.set_state(SubmitStates.waiting_payload)
    await state.update_data(homework_id=homework.id)
    if homework.deadline_ts is not None and now > homework.deadline_ts:
        course = _course()
        rule = _late_rule(course, homework)
        late_days = days_late(now, homework.deadline_ts)
        text = late_submit_warning(homework, late_days, rule)
    else:
        text = submit_prompt(homework)
    if edit:
        await message.edit_text(text)
    else:
        await message.answer(text)


async def _ask_accept(
    message: Message,
    state: FSMContext,
    homework: Assessment,
    payload: str,
    *,
    edit: bool = False,
) -> None:
    await state.update_data(pending_payload=payload, homework_id=homework.id)
    text = accept_confirm_text(homework, payload)
    keyboard = _accept_keyboard(homework)
    if edit:
        await message.edit_text(text, reply_markup=keyboard)
    else:
        await message.answer(text, reply_markup=keyboard)


async def _maybe_confirm_resubmit(
    message: Message,
    state: FSMContext,
    db: Database,
    student: Student,
    homework: Assessment,
    payload: str,
    now: int,
) -> bool:
    previous = await db.latest_submission(student.id, homework.id)
    if previous is None or homework.deadline_ts is None:
        return False
    course = _course()
    rule = _late_rule(course, homework)
    new_days = days_late(now, homework.deadline_ts)
    if not would_add_penalty(
        deadline_ts=homework.deadline_ts,
        previous=previous,
        new_submitted_at=now,
        old_penalty=late_penalty(rule, days_late(previous.submitted_at, homework.deadline_ts)),
        new_penalty=late_penalty(rule, new_days),
    ):
        return False
    await state.set_state(SubmitStates.confirming_resubmit)
    await state.update_data(homework_id=homework.id, payload=payload)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, это новая версия",
                    callback_data=ConfirmResub(action="yes", homework_id=homework.id).pack(),
                ),
                InlineKeyboardButton(
                    text="Нет, оставим прежнюю",
                    callback_data=ConfirmResub(action="no", homework_id=homework.id).pack(),
                ),
            ]
        ]
    )
    await message.answer(resubmit_confirm(homework, previous, new_days, rule), reply_markup=keyboard)
    return True


async def _store_submission(
    message: Message,
    state: FSMContext,
    db: Database,
    student: Student,
    homework: Assessment,
    payload: str,
    now: int,
    settings: Settings,
) -> None:
    previous = await db.latest_submission(student.id, homework.id)
    try:
        submission = await db.add_submission(
            student.id, homework.id, payload, submitted_at=now
        )
    except DeadlineClosedError:
        await state.clear()
        await message.answer(accept_closed_text(homework))
        return
    except NotIssuedError:
        await state.clear()
        await message.answer(not_issued_text(homework))
        return
    except HomeworkNotFoundError as exc:
        await state.clear()
        await message.answer(str(exc))
        return
    except ValueError:
        await message.answer(empty_payload_text())
        return
    await state.clear()
    await message.answer(_accepted_text(homework, submission, previous, now))
    user = message.from_user
    if (
        user is not None
        and student.telegram_id is not None
        and user.id == student.telegram_id
    ):
        student = await db.bind_telegram(student.id, user.id, user.username)
    await _notify_admins_about_submission(
        message, settings, student, homework, submission, previous
    )


async def _notify_admins_about_submission(
    message: Message,
    settings: Settings,
    student: Student,
    homework: Assessment,
    submission: Submission,
    previous: Submission | None,
) -> None:
    bot = message.bot
    if bot is None:
        return
    try:
        await notify_admins_submission(
            bot, settings, student, homework, submission, previous=previous
        )
    except Exception:
        logger.exception("admin submission notice failed")


def _accepted_text(
    homework: Assessment,
    submission: Submission,
    previous: Submission | None,
    now: int,
) -> str:
    _ = now
    deadline = homework.deadline_ts
    if previous is not None:
        if deadline is None or submission.submitted_at <= deadline:
            return accepted_update(homework, submission)
        if previous.submitted_at <= (deadline or previous.submitted_at):
            course = _course()
            rule = _late_rule(course, homework)
            days = days_late(submission.submitted_at, deadline or submission.submitted_at)
            return accepted_late(homework, submission, days, rule)
        return accepted_update(homework, submission)
    if deadline is not None and submission.submitted_at > deadline:
        course = _course()
        rule = _late_rule(course, homework)
        days = days_late(submission.submitted_at, deadline)
        return accepted_late(homework, submission, days, rule)
    return accepted_on_time(homework, submission)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await answer_long(message, help_text())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(cancel_text())


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
        now = now_ts()
        open_now = _open_now(await _submittable(db), now)
        upcoming = upcoming_assessments(await _submittable(db), now)
        await message.answer(
            format_profile(
                student,
                _course(),
                upcoming=upcoming,
                has_current=bool(open_now),
            )
        )
        return
    if is_admin(message.from_user.id, settings):
        if message.bot is not None:
            await setup_commands(message.bot, settings)
        await message.answer(admin_home_text())
        return
    await state.set_state(RegisterStates.waiting_identity)
    await message.answer(welcome_unregistered())


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
                        callback_data=ConfirmReg(action="yes", student_id=unique.id).pack(),
                    ),
                    InlineKeyboardButton(
                        text="Нет, это не я",
                        callback_data=ConfirmReg(action="no", student_id=unique.id).pack(),
                    ),
                ]
            ]
        )
        await message.answer(confirm_one_student(unique), reply_markup=keyboard)
        return
    if result.students:
        buttons = [
            [
                InlineKeyboardButton(
                    text=item.full_name,
                    callback_data=PickIdentity(action="yes", student_id=item.id).pack(),
                )
            ]
            for item in result.students
        ]
        buttons.append(
            [
                InlineKeyboardButton(
                    text="Меня здесь нет",
                    callback_data=PickIdentity(action="none", student_id=0).pack(),
                )
            ]
        )
        await message.answer(
            confirm_many_students(),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
        return
    await message.answer(register_not_found())


async def _finish_bind(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
    student_id: int,
) -> None:
    message = _callback_message(callback)
    if message is None:
        return
    username = callback.from_user.username
    try:
        student = await db.bind_telegram(student_id, callback.from_user.id, username)
    except AlreadyBoundError:
        await message.edit_text(already_bound_text())
        await callback.answer()
        return
    except StudentTakenError:
        await message.edit_text(student_taken_text())
        await callback.answer()
        return
    await state.clear()
    now = now_ts()
    upcoming = upcoming_assessments(await _submittable(db), now)
    await message.edit_text(register_done(student, upcoming))
    await callback.answer("Готово")


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
        await message.edit_text(register_try_again())
        await callback.answer()
        return
    await _finish_bind(callback, state, db, callback_data.student_id)


@router.callback_query(PickIdentity.filter())
async def pick_identity(
    callback: CallbackQuery,
    callback_data: PickIdentity,
    state: FSMContext,
    db: Database,
) -> None:
    message = _callback_message(callback)
    if message is None:
        return
    if callback_data.action != "yes":
        await state.set_state(RegisterStates.waiting_identity)
        await message.edit_text(register_not_found())
        await callback.answer()
        return
    await _finish_bind(callback, state, db, callback_data.student_id)


@router.message(Command("hw"))
async def cmd_hw(message: Message, db: Database) -> None:
    if message.from_user is None:
        return
    student = await db.get_student_by_telegram(message.from_user.id)
    if student is None:
        await message.answer(not_registered_text())
        return
    now = now_ts()
    course = _course()
    assessments = await _submittable(db)
    current = _open_now(assessments, now)
    upcoming = upcoming_assessments(assessments, now)
    if not current:
        await answer_long(message, format_hw_empty_soon(upcoming))
        return
    await message.answer("📌 <b>Сейчас на тебе</b>")
    for index, assessment in enumerate(current):
        submission = await db.latest_submission(student.id, assessment.id)
        grade = await db.get_grade(student.id, assessment.id)
        item = None
        try:
            course_item = course.assessment_by_code(assessment.code)
        except CourseError:
            course_item = None
        if course_item is not None:
            item = build_item(
                course_item,
                course,
                now,
                None if submission is None else submission.submitted_at,
                None if grade is None else grade.score,
            )
        card = format_homework_card(
            assessment, submission, now, course=course, item=item
        )
        if index < len(current) - 1:
            card = f"{card}\n\n—————"
        await answer_long(
            message,
            card,
            reply_markup=_card_keyboard(assessment, submitted=submission is not None),
        )
    if upcoming:
        await answer_long(message, format_soon_block(upcoming))


@router.message(Command("mysubmissions"))
async def cmd_mysubmissions(message: Message, db: Database) -> None:
    if message.from_user is None:
        return
    student = await db.get_student_by_telegram(message.from_user.id)
    if student is None:
        await message.answer(not_registered_text())
        return
    items = await db.list_student_submissions(student.id)
    if not items:
        await message.answer(format_mysubmissions([]))
        return
    now = now_ts()
    course = _course()
    known = {entry.code: entry for entry in course.assessments}
    cards: list[tuple[Assessment, Submission, ItemResult | None]] = []
    for assessment, submission in items:
        item = None
        course_item = known.get(assessment.code)
        grade = await db.get_grade(student.id, assessment.id)
        if course_item is not None:
            item = build_item(
                course_item,
                course,
                now,
                submission.submitted_at,
                None if grade is None else grade.score,
            )
        cards.append((assessment, submission, item))
    await answer_long(message, format_mysubmissions(cards))


@router.message(Command("submit"))
async def cmd_submit(message: Message, state: FSMContext, db: Database) -> None:
    if message.from_user is None:
        return
    student = await db.get_student_by_telegram(message.from_user.id)
    if student is None:
        await message.answer(not_registered_text())
        return
    now = now_ts()
    open_homeworks = _open_now(await _submittable(db), now)
    if not open_homeworks:
        upcoming = upcoming_assessments(await _submittable(db), now)
        await message.answer(format_hw_empty_soon(upcoming))
        return
    if len(open_homeworks) == 1:
        await _prompt_for_homework(message, state, open_homeworks[0])
        return
    await message.answer(submit_choose(), reply_markup=_submit_keyboard(open_homeworks))


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
    if homework.issued_at is not None and now < homework.issued_at:
        await message.edit_text(not_issued_text(homework))
        await callback.answer()
        return
    if homework.accept_until_ts is not None and homework.accept_until_ts < now:
        await message.edit_text(accept_closed_text(homework))
        await callback.answer()
        return
    data = await state.get_data()
    pending = data.get("pending_payload")
    if isinstance(pending, str) and pending.strip():
        if await _maybe_confirm_resubmit(
            message, state, db, student, homework, pending, now
        ):
            await callback.answer()
            return
        await _ask_accept(message, state, homework, pending, edit=True)
        await callback.answer()
        return
    await _prompt_for_homework(message, state, homework, edit=True)
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
        await message.answer(not_registered_text())
        return
    data = await state.get_data()
    homework_id = data.get("homework_id")
    if not isinstance(homework_id, int):
        await state.clear()
        await message.answer(lost_thread_text())
        return
    homework = await db.get_homework(homework_id)
    if homework is None:
        await state.clear()
        await message.answer(lost_thread_text())
        return
    now = now_ts()
    if await _maybe_confirm_resubmit(
        message, state, db, student, homework, message.text, now
    ):
        return
    await _ask_accept(message, state, homework, message.text)


@router.callback_query(ConfirmResub.filter())
async def confirm_resubmit(
    callback: CallbackQuery,
    callback_data: ConfirmResub,
    state: FSMContext,
    db: Database,
    settings: Settings,
) -> None:
    message = _callback_message(callback)
    if message is None:
        return
    student = await db.get_student_by_telegram(callback.from_user.id)
    if student is None:
        await callback.answer("Сначала /start", show_alert=True)
        return
    if callback_data.action != "yes":
        await state.clear()
        await message.edit_text(cancel_text())
        await callback.answer()
        return
    data = await state.get_data()
    payload = data.get("payload")
    if not isinstance(payload, str):
        await state.clear()
        await message.edit_text(lost_thread_text())
        await callback.answer()
        return
    homework = await db.get_homework(callback_data.homework_id)
    if homework is None:
        await state.clear()
        await message.edit_text(lost_thread_text())
        await callback.answer()
        return
    await message.edit_reply_markup(reply_markup=None)
    await _store_submission(
        message, state, db, student, homework, payload, now_ts(), settings
    )
    await callback.answer()


@router.callback_query(OfferSubmit.filter())
async def offer_submit(
    callback: CallbackQuery,
    callback_data: OfferSubmit,
    state: FSMContext,
    db: Database,
    settings: Settings,
) -> None:
    message = _callback_message(callback)
    if message is None:
        return
    if callback_data.action == "no":
        await state.clear()
        await message.edit_text(fallback_generic())
        await callback.answer()
        return
    student = await db.get_student_by_telegram(callback.from_user.id)
    if student is None:
        await callback.answer("Сначала /start", show_alert=True)
        return
    data = await state.get_data()
    payload = data.get("pending_payload")
    if not isinstance(payload, str):
        await state.clear()
        await message.edit_text(lost_thread_text())
        await callback.answer()
        return
    homework = await db.get_homework(callback_data.homework_id)
    if homework is None:
        await state.clear()
        await message.edit_text(lost_thread_text())
        await callback.answer()
        return
    now = now_ts()
    await message.edit_reply_markup(reply_markup=None)
    if await _maybe_confirm_resubmit(message, state, db, student, homework, payload, now):
        await callback.answer()
        return
    await _store_submission(message, state, db, student, homework, payload, now, settings)
    await callback.answer()


@router.message(Command("grade"))
async def cmd_grade(message: Message, db: Database) -> None:
    if message.from_user is None:
        return
    student = await db.get_student_by_telegram(message.from_user.id)
    if student is None:
        await message.answer(not_registered_text())
        return
    course = _course()
    state = await build_student_state(db, student, course)
    report = build_report(state, course, now_ts())
    await answer_long(message, format_grade_report(report, course))


def _attendance_payload(
    student: Student, state: StudentState, course: Course
) -> tuple[str, InlineKeyboardMarkup | None]:
    lessons = list(student_lessons(course, student.seminar_group))
    present, absent, excused = count_attendance(
        lessons,
        state.attendance,
        held_codes=state.held_lesson_codes,
        missing_as_absent=True,
    )
    percent = attendance_percent(present, absent, excused)
    score = attendance_score(present, absent, excused, course.attendance_scale)
    held = present + absent + excused
    remaining = max(0, len(lessons) - held)
    forecast = None
    if remaining > 0:
        forecast = attendance_score(
            present + remaining, absent, excused, course.attendance_scale
        )
    absences = [
        lesson
        for lesson in lessons
        if lesson.code in state.held_lesson_codes
        and state.attendance.get(lesson.code) != "present"
        and state.attendance.get(lesson.code) != "excused"
    ]
    text = format_attendance_summary(
        present=present,
        absent=absent,
        excused=excused,
        held=held,
        percent=percent,
        score=score,
        remaining=remaining,
        forecast_if_attend=forecast,
        absences=absences,
        seminar_unknown=student.seminar_group is None,
        no_held=held == 0,
    )
    markup = None
    if lessons:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"Показать все {len(lessons)} занятия",
                        callback_data=ShowAttendance(action="all").pack(),
                    )
                ]
            ]
        )
    return text, markup


@router.message(Command("db"))
async def cmd_db_access(message: Message, db: Database) -> None:
    if message.from_user is None:
        return
    student = await db.get_student_by_telegram(message.from_user.id)
    if student is None:
        await message.answer(not_registered_text())
        return
    if not student.db_login or not student.db_password:
        await message.answer(db_access_missing_text())
        return
    await message.answer(db_access_text(student), disable_web_page_preview=True)


@router.message(Command("attendance"))
async def cmd_attendance(message: Message, db: Database) -> None:
    if message.from_user is None:
        return
    student = await db.get_student_by_telegram(message.from_user.id)
    if student is None:
        await message.answer(not_registered_text())
        return
    course = _course()
    state = await build_student_state(db, student, course)
    text, markup = _attendance_payload(student, state, course)
    await answer_long(message, text, reply_markup=markup)


@router.callback_query(ShowAttendance.filter())
async def show_all_attendance(
    callback: CallbackQuery,
    callback_data: ShowAttendance,
    db: Database,
) -> None:
    _ = callback_data
    message = _callback_message(callback)
    if message is None:
        return
    student = await db.get_student_by_telegram(callback.from_user.id)
    if student is None:
        await callback.answer("Сначала /start", show_alert=True)
        return
    course = _course()
    state = await build_student_state(db, student, course)
    lessons = list(student_lessons(course, student.seminar_group))
    text = format_attendance_full_list(
        lessons, dict(state.attendance), state.held_lesson_codes
    )
    await answer_long(message, text)
    await callback.answer()


# ── Команды итогового проекта ─────────────────────────────────────────
# В команду попадают только по приглашению: нашёл однокурсника по фамилии
# или в списке «Кто без команды», позвал, тот принял. Команда появляется,
# когда принял второй, и распадается, когда пара расходится. После lock_at
# всё только для чтения — состав меняет преподаватель через CLI.

PAGE_SIZE = 8
GONE_TEXT = "Это приглашение уже не действует."


class TeamStates(StatesGroup):
    search = State()
    name = State()


class TeamMenu(CallbackData, prefix="tm"):
    action: str  # card | list | find | name | leave
    page: int = 0


class TeamPick(CallbackData, prefix="tp"):
    # Данные кнопки присылает клиент, поэтому всё проверяется заново: позвать
    # так можно только того, кого и так видно в поиске.
    student_id: int


class InviteAnswer(CallbackData, prefix="ti"):
    # Только номер приглашения: вправе ли нажавший ответить, проверяет база.
    action: str  # yes | no | cancel
    invite_id: int


class LeaveTeam(CallbackData, prefix="tl"):
    action: str


Button = tuple[str, CallbackData]


def _row(*buttons: Button) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=text, callback_data=data.pack()) for text, data in buttons]


def _markup(rows: Sequence[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup | None:
    return InlineKeyboardMarkup(inline_keyboard=list(rows)) if rows else None


async def _edit(message: Message, text: str, markup: InlineKeyboardMarkup | None) -> None:
    """Перерисовать сообщение. Повторное нажатие с тем же видом — не ошибка."""
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


async def _notify_team(message: Message, members: Sequence[Student], text: str) -> None:
    bot = message.bot
    if bot is None or not members:
        return
    await send_to_students(bot, members, text)


async def _team_state(
    db: Database, me: Student
) -> tuple[Team | None, set[int], list[Invite], dict[int, Student]]:
    """Команда, её участники (у одиночки — он сам), приглашения и все студенты."""
    team = await db.team_of_student(me.id)
    mine = {member.id for member in team.members} if team is not None else {me.id}
    people = {student.id: student for student in await db.list_students()}
    return team, mine, await db.list_invites(), people


async def _card(db: Database, me: Student) -> tuple[str, InlineKeyboardMarkup | None]:
    teams = _course().teams
    now = now_ts()
    team, mine, invites, people = await _team_state(db, me)
    incoming = [
        (invite, people[invite.inviter_id])
        for invite in invites
        if team is None and invite.invitee_id == me.id
    ]
    outgoing = [(invite, people[invite.invitee_id]) for invite in invites if invite.inviter_id in mine]
    text = format_team_card(team, incoming, outgoing, teams, now)
    if not teams.is_open(now):
        return text, None
    rows = [
        _row(
            (f"Принять: {short_name(inviter.full_name)}", InviteAnswer(action="yes", invite_id=invite.id)),
            ("Отказаться", InviteAnswer(action="no", invite_id=invite.id)),
        )
        for invite, inviter in incoming
    ]
    rows.extend(
        _row(
            (f"Отозвать: {short_name(invitee.full_name)}", InviteAnswer(action="cancel", invite_id=invite.id))
        )
        for invite, invitee in outgoing
        if invite.inviter_id == me.id
    )
    if len(mine) + len(outgoing) < teams.max_size:
        rows.append(
            _row(("Найти по фамилии", TeamMenu(action="find")), ("Кто без команды", TeamMenu(action="list")))
        )
    if team is not None:
        rows.append(
            _row(("Название", TeamMenu(action="name")), ("Выйти из команды", TeamMenu(action="leave")))
        )
    return text, _markup(rows)


async def _show_card(message: Message, db: Database, me: Student) -> None:
    text, markup = await _card(db, me)
    await _edit(message, text, markup)


async def _people_view(
    db: Database, me: Student, shown: Sequence[Student]
) -> tuple[list[tuple[Student, str]], list[list[InlineKeyboardButton]], bool]:
    """Строки со статусами, кнопки и есть ли у тебя свободное место."""
    teams = _course().teams
    _team, mine, invites, _people = await _team_state(db, me)
    in_team = {member.id for item in await db.list_teams() for member in item.members}
    sent_to = {invite.invitee_id for invite in invites if invite.inviter_id in mine}
    calling_me = {invite.inviter_id: invite for invite in invites if invite.invitee_id == me.id}
    room = len(mine) + len(sent_to) < teams.max_size
    rows: list[tuple[Student, str]] = []
    buttons: list[list[InlineKeyboardButton]] = []
    for person in shown:
        status = ""
        if person.id in in_team:
            status = "уже в команде"
        elif person.id in calling_me:
            status = "зовёт тебя"
            buttons.append(
                _row(
                    (
                        f"Принять: {short_name(person.full_name)}",
                        InviteAnswer(action="yes", invite_id=calling_me[person.id].id),
                    )
                )
            )
        elif person.id in sent_to:
            status = "приглашение отправлено"
        elif person.telegram_id is None:
            status = "ещё не в боте"
        elif room:
            buttons.append(_row((f"Пригласить: {short_name(person.full_name)}", TeamPick(student_id=person.id))))
        rows.append((person, status))
    return rows, buttons, room


async def _show_list(message: Message, db: Database, me: Student, page: int) -> None:
    in_team = {member.id for team in await db.list_teams() for member in team.members}
    free = sorted(
        (item for item in await db.list_students() if item.id != me.id and item.id not in in_team),
        key=lambda item: normalize_text(item.full_name),
    )
    pages = max(1, -(-len(free) // PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    shown = free[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    rows, buttons, room = await _people_view(db, me, shown)
    nav: list[Button] = []
    if page > 0:
        nav.append(("←", TeamMenu(action="list", page=page - 1)))
    nav.append(("К команде", TeamMenu(action="card")))
    if page < pages - 1:
        nav.append(("→", TeamMenu(action="list", page=page + 1)))
    footer = team_no_room_text() if shown and not room else ""
    await _edit(
        message,
        format_people(team_list_heading(len(free), page, pages), rows, footer),
        _markup([*buttons, _row(*nav)]),
    )


async def _notify_dropped(
    message: Message, db: Database, dropped: Sequence[Invite], *, actor: Student
) -> None:
    """Сказать второй стороне погасших приглашений, почему они погасли."""
    if not dropped:
        return
    people = {student.id: student for student in await db.list_students()}
    team_of = {member.id: team.id for team in await db.list_teams() for member in team.members}
    for invite in dropped:
        inviter, invitee = people[invite.inviter_id], people[invite.invitee_id]
        together = invite.inviter_id in team_of and team_of[invite.inviter_id] == team_of.get(
            invite.invitee_id
        )
        if together:
            continue
        if invite.invitee_id in team_of:
            recipient, text = inviter, team_invite_gone_notice(invitee, to_inviter=True)
        else:
            recipient, text = invitee, team_invite_gone_notice(inviter, to_inviter=False)
        if recipient.id != actor.id:
            await _notify_team(message, [recipient], text)


async def _student_for(callback: CallbackQuery, db: Database) -> Student | None:
    student = await db.get_student_by_telegram(callback.from_user.id)
    if student is None:
        await callback.answer("Сначала /start", show_alert=True)
    return student


@router.message(Command("team"))
async def cmd_team(message: Message, state: FSMContext, db: Database) -> None:
    if message.from_user is None:
        return
    student = await db.get_student_by_telegram(message.from_user.id)
    if student is None:
        await message.answer(not_registered_text())
        return
    await state.set_state(None)
    text, markup = await _card(db, student)
    await message.answer(text, reply_markup=markup)


@router.callback_query(TeamMenu.filter())
async def team_menu(
    callback: CallbackQuery,
    callback_data: TeamMenu,
    state: FSMContext,
    db: Database,
) -> None:
    message = _callback_message(callback)
    if message is None:
        return
    me = await _student_for(callback, db)
    if me is None:
        return
    # set_state(None), а не clear(): черновик сдачи в данных диалога остаётся
    await state.set_state(None)
    teams = _course().teams
    action = callback_data.action
    back = _markup([_row(("Назад", TeamMenu(action="card")))])
    if action != "card" and not teams.is_open(now_ts()):
        await _show_card(message, db, me)
        await callback.answer(teams_locked_text(teams), show_alert=True)
        return
    team = await db.team_of_student(me.id)
    if action == "list":
        await _show_list(message, db, me, callback_data.page)
    elif action == "find":
        await state.set_state(TeamStates.search)
        await _edit(message, team_search_prompt(), back)
    elif action == "name" and team is not None:
        await state.set_state(TeamStates.name)
        await _edit(message, team_name_prompt(team.name), back)
    elif action == "leave" and team is not None:
        await _edit(
            message,
            team_leave_confirm_text(team),
            _markup([_row(("Да, выйти", LeaveTeam(action="yes")), ("Нет, остаюсь", LeaveTeam(action="no")))]),
        )
    else:
        await _show_card(message, db, me)
    await callback.answer()


@router.message(StateFilter(TeamStates.search), F.text, PlainText())
async def team_search(message: Message, state: FSMContext, db: Database) -> None:
    query = (message.text or "").strip()
    if looks_like_submission(query):
        # ссылку на работу прислали посреди поиска — это сдача, а не фамилия
        await state.set_state(None)
        raise SkipHandler()
    if message.from_user is None:
        return
    me = await db.get_student_by_telegram(message.from_user.id)
    teams = _course().teams
    if me is None or not teams.is_open(now_ts()):
        await state.set_state(None)
        await message.answer(not_registered_text() if me is None else teams_locked_text(teams))
        return
    if len(normalize_text(query).replace(" ", "")) < 2:
        await message.answer(team_search_short_text())
        return
    await state.set_state(None)
    found = search_students(query, [item for item in await db.list_students() if item.id != me.id])
    if not found:
        await message.answer(
            team_search_empty_text(query),
            reply_markup=_markup(
                [_row(("Искать ещё", TeamMenu(action="find")), ("Кто без команды", TeamMenu(action="list")))]
            ),
        )
        return
    shown = found[:PAGE_SIZE]
    rows, buttons, room = await _people_view(db, me, shown)
    await message.answer(
        format_people(
            team_search_heading(query, len(found), len(shown)),
            rows,
            "" if room else team_no_room_text(),
        ),
        reply_markup=_markup(
            [*buttons, _row(("Искать ещё", TeamMenu(action="find")), ("К команде", TeamMenu(action="card")))]
        ),
    )


@router.message(StateFilter(TeamStates.name), F.text, PlainText())
async def team_rename(message: Message, state: FSMContext, db: Database) -> None:
    raw = message.text or ""
    if looks_like_submission(raw):
        await state.set_state(None)
        raise SkipHandler()
    if message.from_user is None:
        return
    me = await db.get_student_by_telegram(message.from_user.id)
    if me is None:
        await state.set_state(None)
        await message.answer(not_registered_text())
        return
    team = await db.team_of_student(me.id)
    if team is None or not _course().teams.is_open(now_ts()):
        await state.set_state(None)
        text, markup = await _card(db, me)
        await message.answer(text, reply_markup=markup)
        return
    try:
        name = clean_team_name(raw)
    except ValueError:
        name = None
    if name is None:
        await message.answer(team_name_bad_text())
        return
    await db.rename_team(team.id, name)
    await state.set_state(None)
    await _notify_team(
        message, [member for member in team.members if member.id != me.id], team_renamed_notice(name)
    )
    text, markup = await _card(db, me)
    await message.answer(text, reply_markup=markup)


@router.callback_query(TeamPick.filter())
async def team_pick(callback: CallbackQuery, callback_data: TeamPick, db: Database) -> None:
    message = _callback_message(callback)
    if message is None:
        return
    me = await _student_for(callback, db)
    if me is None:
        return
    teams = _course().teams
    if not teams.is_open(now_ts()):
        alert = teams_locked_text(teams)
    else:
        alert = await _invite(message, db, me, await db.get_student(callback_data.student_id))
    await _show_card(message, db, me)
    await callback.answer(alert, show_alert=True)


async def _invite(message: Message, db: Database, me: Student, target: Student | None) -> str:
    """Отправить приглашение. Возвращает текст всплывающей подсказки (без разметки)."""
    if target is None or target.id == me.id or target.telegram_id is None:
        return "Этого человека позвать нельзя: его нет в боте курса."
    who = short_name(target.full_name)
    invites = await db.list_invites()
    if any(item.inviter_id == target.id and item.invitee_id == me.id for item in invites):
        return f"{who}: уже зовёт тебя. Прими приглашение в карточке команды."
    try:
        invite = await db.send_invite(me.id, target.id, max_size=_course().teams.max_size)
    except AlreadyInTeamError:
        return f"{who}: уже в команде."
    except TeamFullError:
        return "Свободных мест нет: дождись ответа на приглашения или отзови одно из них."
    if invite is None:
        return f"{who}: приглашение уже отправлено, ждём ответа."
    bot = message.bot
    delivered = bot is not None
    if bot is not None:
        try:
            await bot.send_message(
                target.telegram_id,
                team_invite_text(me, await db.team_of_student(me.id), target),
                reply_markup=_markup(
                    [
                        _row(
                            ("Принять", InviteAnswer(action="yes", invite_id=invite.id)),
                            ("Отказаться", InviteAnswer(action="no", invite_id=invite.id)),
                        )
                    ]
                ),
            )
        except TelegramAPIError:
            logger.warning("team invite not delivered, student_id=%s", target.id)
            delivered = False
    if not delivered:
        return (
            f"Приглашение сохранено, но сообщение не доставлено ({who}): похоже, бот "
            "остановлен. Приглашение всё равно видно в /team."
        )
    return f"{who}: приглашение отправлено, ответ придёт сюда."


@router.callback_query(InviteAnswer.filter())
async def invite_answer(
    callback: CallbackQuery, callback_data: InviteAnswer, db: Database
) -> None:
    message = _callback_message(callback)
    if message is None:
        return
    me = await _student_for(callback, db)
    if me is None:
        return
    teams = _course().teams
    if not teams.is_open(now_ts()):
        await _show_card(message, db, me)
        await callback.answer(teams_locked_text(teams), show_alert=True)
        return
    invite_id = callback_data.invite_id
    if callback_data.action == "yes":
        text, refused = await _accept(message, db, me, invite_id)
    else:
        as_inviter = callback_data.action == "cancel"
        invite = await db.take_invite(invite_id, me.id, as_inviter=as_inviter)
        refused = invite is None
        if invite is None:
            text = GONE_TEXT
        elif as_inviter:
            text = "Приглашение отозвано."
            invitee = await db.get_student(invite.invitee_id)
            if invitee is not None:
                await _notify_team(message, [invitee], team_cancelled_notice(me))
        else:
            text = "Приглашение отклонено."
            inviter = await db.get_student(invite.inviter_id)
            if inviter is not None:
                await _notify_team(message, [inviter], team_declined_notice(me))
    await _show_card(message, db, me)
    await callback.answer(text, show_alert=refused)


async def _accept(message: Message, db: Database, me: Student, invite_id: int) -> tuple[str, bool]:
    """Принять приглашение: текст подсказки и был ли отказ."""
    try:
        team, dropped = await db.accept_invite(invite_id, me.id, max_size=_course().teams.max_size)
    except InviteGoneError:
        return GONE_TEXT, True
    except AlreadyInTeamError:
        return "Ты уже в команде: состоять можно только в одной.", True
    except (TeamFullError, TeamNotFoundError):
        return "В этой команде уже нет мест.", True
    others = [member for member in team.members if member.id != me.id]
    await _notify_team(message, others, team_member_joined_notice(me, team))
    await _notify_dropped(message, db, dropped, actor=me)
    return f"Готово: ты в команде, вас {len(team.members)}.", False


@router.callback_query(LeaveTeam.filter())
async def leave_team(
    callback: CallbackQuery,
    callback_data: LeaveTeam,
    db: Database,
) -> None:
    message = _callback_message(callback)
    if message is None:
        return
    me = await _student_for(callback, db)
    if me is None:
        return
    teams = _course().teams
    if callback_data.action != "yes":
        await _show_card(message, db, me)
        await callback.answer()
        return
    if not teams.is_open(now_ts()):
        await _show_card(message, db, me)
        await callback.answer(teams_locked_text(teams), show_alert=True)
        return
    try:
        team, dropped = await db.leave_team(me.id, dissolve=True)
    except NotInTeamError:
        await _show_card(message, db, me)
        await callback.answer("Ты уже не в команде.")
        return
    remaining = [member for member in team.members if member.id != me.id]
    if len(remaining) == 1:
        await _notify_team(message, remaining, team_dissolved_notice(me, teams))
    else:
        await _notify_team(message, remaining, team_member_left_notice(me, len(remaining)))
    await _notify_dropped(message, db, dropped, actor=me)
    await _show_card(message, db, me)
    await callback.answer("Готово: ты больше не в команде.")


@router.message(StateFilter(RegisterStates.waiting_identity))
async def register_need_text_handler(message: Message) -> None:
    if message.document is not None or message.photo:
        await message.answer(file_not_accepted_text())
        return
    await message.answer(register_need_text())


@router.message(StateFilter(SubmitStates.waiting_payload))
async def submit_need_text_handler(message: Message) -> None:
    if message.document is not None or message.photo:
        await message.answer(file_not_accepted_text())
        return
    await message.answer(submit_need_text())


@fallback_router.message(F.document | F.photo)
async def reject_files(message: Message) -> None:
    await message.answer(file_not_accepted_text())


@fallback_router.message(F.text, PlainText())
async def fallback_text(message: Message, state: FSMContext, db: Database) -> None:
    if message.from_user is None or message.text is None:
        return
    student = await db.get_student_by_telegram(message.from_user.id)
    if student is None:
        await message.answer(not_registered_text())
        return
    now = now_ts()
    open_homeworks = _open_now(await _submittable(db), now)
    reply = fallback_reply(message.text, open_homeworks)
    if reply == fallback_generic():
        await message.answer(reply)
        return
    await state.update_data(pending_payload=message.text)
    if len(open_homeworks) == 1:
        homework = open_homeworks[0]
        await message.answer(
            stub_one_work(homework, message.text),
            reply_markup=_accept_keyboard(homework),
        )
        return
    buttons = [
        [
            InlineKeyboardButton(
                text=work_name(item),
                callback_data=OfferSubmit(action="yes", homework_id=item.id).pack(),
            )
        ]
        for item in open_homeworks
    ]
    buttons.append(
        [
            InlineKeyboardButton(
                text="Это не сдача",
                callback_data=OfferSubmit(action="no", homework_id=0).pack(),
            )
        ]
    )
    await message.answer(
        stub_many_works(message.text),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@fallback_router.message()
async def fallback_anything(message: Message) -> None:
    await message.answer(fallback_generic())
