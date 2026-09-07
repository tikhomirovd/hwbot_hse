from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from hwbot.bot import run_bot
from hwbot.config import load_settings
from hwbot.course import DEFAULT_COURSE_PATH, load_course
from hwbot.db import Database
from hwbot.errors import HomeworkNotFoundError
from hwbot.export import format_status_text, gradebook_csv, status_csv
from hwbot.groups import canonical_seminar_group
from hwbot.notify import broadcast_text
from hwbot.models import Student
from hwbot.overview import (
    ActionSummary,
    AssessmentOverview,
    AttendanceGapKind,
    CourseOverview,
    LessonAttendanceGap,
    build_course_overview,
)
from hwbot.ops import (
    MatchFailure,
    WriteReport,
    apply_attendance,
    apply_grades,
    apply_roster_seminar_groups,
    preview_seed,
    read_named_csv,
    reports_for_students,
    resolve_all,
    resolve_student,
    seed_course,
    split_names,
)
from hwbot.roster import load_roster
from hwbot.timeutil import format_dt, now_ts


async def _with_db(db_path: Path) -> Database:
    db = Database(db_path)
    await db.connect()
    return db


async def cmd_run() -> int:
    await run_bot()
    return 0


async def cmd_import_roster() -> int:
    settings = load_settings()
    db = await _with_db(settings.db_path)
    try:
        rows = load_roster(settings.roster_path)
        await db.seed_roster(rows)
        print(f"Импортировано студентов: {len(rows)}")
        return 0
    finally:
        await db.close()


async def cmd_broadcast(text: str) -> int:
    settings = load_settings()
    db = await _with_db(settings.db_path)
    bot = Bot(
        settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        sent = await broadcast_text(bot, db, text)
        print(f"Разослал {sent} студентам")
        return 0
    finally:
        await bot.session.close()
        await db.close()


def format_students_listing(
    students: Sequence[Student], *, registered: bool | None
) -> str:
    bound = [item for item in students if item.telegram_id is not None]
    missing = [item for item in students if item.telegram_id is None]
    total = len(students)
    if registered is True:
        header = f"зарегистрировано {len(bound)} из {total}"
        shown = bound
    elif registered is False:
        header = f"не зарегистрированы: {len(missing)} из {total}"
        shown = missing
    else:
        header = f"зарегистрировано {len(bound)} из {total}"
        shown = missing
        if missing:
            header = f"{header}\nне зарегистрированы:"
    lines = [header]
    for student in shown:
        mark = "да" if student.telegram_id is not None else "нет"
        lines.append(f"{student.full_name}\t{student.group_code}\t{mark}")
    return "\n".join(lines)


async def cmd_students(registered: bool | None) -> int:
    settings = load_settings()
    db = await _with_db(settings.db_path)
    try:
        print(format_students_listing(await db.list_students(), registered=registered))
        return 0
    finally:
        await db.close()


def _overview_assessment_line(item: AssessmentOverview) -> str:
    head = f"{item.label} [{item.code}]"
    if item.deadline_ts is not None:
        head = f"{head} дедлайн {format_dt(item.deadline_ts)}"
    if not item.open_for_submissions:
        head = f"{head} приём закрыт"
    not_submitted = "пока не сдали" if item.open_for_submissions else "не сдали"
    parts = [
        f"сдали {item.submitted}",
        f"{not_submitted} {item.missing}",
        f"проверено {item.reviewed}",
        f"ждут проверки {item.awaiting_review}",
    ]
    return f"{head}\t{', '.join(parts)}"


def _overview_gap_line(gap: LessonAttendanceGap) -> str:
    state = (
        f"отметок нет, ожидается {gap.expected}"
        if gap.state is AttendanceGapKind.NOT_ENTERED
        else f"без отметки {gap.unmarked} из {gap.expected}"
    )
    group = "" if gap.seminar_group is None else f" [{gap.seminar_group}]"
    return f"{gap.code}{group}\t{gap.title}\t{state}"


def _overview_action_lines(actions: ActionSummary) -> list[str]:
    if not actions.has_actions:
        return ["ручных действий сейчас нет"]
    counters = (
        ("не зарегистрированы", actions.unregistered),
        ("ждут проверки", actions.awaiting_review),
        ("занятия без отметок", actions.lessons_without_attendance),
        ("занятия отмечены частично", actions.lessons_with_partial_attendance),
        ("семинарская группа неизвестна", actions.students_with_unknown_seminar_group),
    )
    return [f"{title}\t{count}" for title, count in counters if count]


def format_overview_text(overview: CourseOverview) -> str:
    registration = overview.registration
    lines = [
        f"обзор на {format_dt(overview.as_of_ts)}",
        "",
        f"регистрация: {registration.registered} из {registration.total}, "
        f"не зашли {registration.unregistered}",
        "",
    ]
    if overview.assessments:
        lines.append("работы в работе:")
        lines.extend(_overview_assessment_line(item) for item in overview.assessments)
    else:
        lines.append("работ в работе нет")
    attendance = overview.attendance
    lines.extend(
        [
            "",
            f"посещаемость: прошло занятий {attendance.ended_lessons}, "
            f"отмечено полностью {attendance.fully_marked_lessons}",
        ]
    )
    lines.extend(_overview_gap_line(gap) for gap in attendance.gaps)
    if attendance.students_with_unknown_seminar_group:
        lines.append(
            "семинарская группа неизвестна у "
            f"{attendance.students_with_unknown_seminar_group}: "
            "они не попадают ни в один семинар"
        )
    lines.extend(["", "нужно внимание:"])
    lines.extend(_overview_action_lines(overview.actions))
    return "\n".join(lines)


async def cmd_overview() -> int:
    settings = load_settings()
    db = await _with_db(settings.db_path)
    try:
        print(format_overview_text(await build_course_overview(db, now_ts())))
        return 0
    finally:
        await db.close()


async def cmd_unbind(student_query: str) -> int:
    settings = load_settings()
    db = await _with_db(settings.db_path)
    try:
        matched = resolve_student(student_query, await db.list_students())
        if isinstance(matched, MatchFailure):
            print(f"{matched.query}: {matched.reason}", file=sys.stderr)
            return 1
        await db.unbind_telegram(matched.id)
        print("Отвязал")
        return 0
    finally:
        await db.close()


async def cmd_list_hw() -> int:
    settings = load_settings()
    db = await _with_db(settings.db_path)
    try:
        homeworks = await db.list_homeworks()
        if not homeworks:
            print("ДЗ нет")
            return 0
        for hw in homeworks:
            print(f"#{hw.id}\t{hw.code}\t{hw.title}\t{hw.deadline_ts}")
        return 0
    finally:
        await db.close()


async def cmd_status(homework_id: int) -> int:
    settings = load_settings()
    db = await _with_db(settings.db_path)
    try:
        homework = await db.get_homework(homework_id)
        if homework is None:
            raise HomeworkNotFoundError("Задание не найдено")
        rows = await db.homework_status(homework_id)
        print(format_status_text(homework, rows))
        return 0
    except HomeworkNotFoundError:
        print("Такого ДЗ нет", file=sys.stderr)
        return 1
    finally:
        await db.close()


async def cmd_export(homework_id: int, output: Path | None) -> int:
    settings = load_settings()
    db = await _with_db(settings.db_path)
    try:
        homework = await db.get_homework(homework_id)
        if homework is None:
            raise HomeworkNotFoundError("Задание не найдено")
        rows = await db.homework_status(homework_id)
        csv_text = status_csv(homework, rows)
        if output is None:
            sys.stdout.write(csv_text)
        else:
            output.write_text(csv_text, encoding="utf-8")
            print(f"Записал {output}")
        return 0
    except HomeworkNotFoundError:
        print("Такого ДЗ нет", file=sys.stderr)
        return 1
    finally:
        await db.close()


def _print_failures(failed: Sequence[MatchFailure]) -> None:
    for item in failed:
        print(f"{item.query}: {item.reason}", file=sys.stderr)


def _print_write_report(report: WriteReport, *, dry_run: bool) -> None:
    prefix = "dry-run, " if dry_run else ""
    print(f"{prefix}создано {report.created}, обновлено {report.updated}")
    for warning in report.warnings:
        print(warning)


async def cmd_seed_course(path: Path, dry_run: bool) -> int:
    settings = load_settings()
    course = load_course(path)
    db = await _with_db(settings.db_path)
    try:
        report = await (preview_seed(db, course) if dry_run else seed_course(db, course))
        prefix = "dry-run, " if dry_run else ""
        print(
            f"{prefix}занятия: создано {report.created_lessons}, "
            f"обновлено {report.updated_lessons}, "
            f"без изменений {report.unchanged_lessons}"
        )
        print(
            f"{prefix}элементы: создано {report.created_assessments}, "
            f"обновлено {report.updated_assessments}, "
            f"без изменений {report.unchanged_assessments}"
        )
        for change in report.changes:
            print(change)
        return 0
    finally:
        await db.close()


async def cmd_attendance_mark(
    lesson_code: str,
    present: str,
    absent: str,
    excused: str,
) -> int:
    settings = load_settings()
    db = await _with_db(settings.db_path)
    try:
        lesson = await db.get_lesson_by_code(lesson_code)
        if lesson is None:
            print(f"Нет занятия {lesson_code}", file=sys.stderr)
            return 1
        students = await db.list_students()
        planned: list[tuple[str, str]] = []
        planned.extend((name, "present") for name in split_names(present))
        planned.extend((name, "absent") for name in split_names(absent))
        planned.extend((name, "excused") for name in split_names(excused))
        if not planned:
            print("Нужен хотя бы один студент", file=sys.stderr)
            return 1
        found, failed = resolve_all([name for name, _status in planned], students)
        if failed:
            _print_failures(failed)
            return 1
        assignments = list(zip(found, [status for _name, status in planned], strict=True))
        report = await apply_attendance(db, lesson, assignments, dry_run=False)
        _print_write_report(report, dry_run=False)
        return 0
    finally:
        await db.close()


async def cmd_attendance_import(lesson_code: str, path: Path, dry_run: bool) -> int:
    settings = load_settings()
    db = await _with_db(settings.db_path)
    try:
        lesson = await db.get_lesson_by_code(lesson_code)
        if lesson is None:
            print(f"Нет занятия {lesson_code}", file=sys.stderr)
            return 1
        key, rows = read_named_csv(path)
        students = await db.list_students()
        queries = [row[key] for row in rows]
        found, failed = resolve_all(queries, students)
        if failed:
            _print_failures(failed)
            return 1
        assignments: list[tuple[Student, str]] = []
        for student, row in zip(found, rows, strict=True):
            status = row.get("status", "").strip()
            if status not in {"present", "absent", "excused"}:
                print(f"Неизвестный статус: {status}", file=sys.stderr)
                return 1
            assignments.append((student, status))
        report = await apply_attendance(db, lesson, assignments, dry_run=dry_run)
        _print_write_report(report, dry_run=dry_run)
        return 0
    finally:
        await db.close()


async def cmd_attendance_show(lesson_code: str | None, student_query: str | None) -> int:
    settings = load_settings()
    db = await _with_db(settings.db_path)
    try:
        if student_query:
            students = await db.list_students()
            matched = resolve_student(student_query, students)
            if isinstance(matched, MatchFailure):
                print(f"{matched.query}: {matched.reason}", file=sys.stderr)
                return 1
            marks = await db.list_attendance_for_student(matched.id)
            lessons = {lesson.id: lesson for lesson in await db.list_lessons()}
            if not marks:
                print("Отметок нет")
                return 0
            for mark in marks:
                lesson = lessons.get(mark.lesson_id)
                code = lesson.code if lesson is not None else str(mark.lesson_id)
                print(f"{code}\t{mark.status}")
            return 0
        if not lesson_code:
            print("Нужен --lesson или --student", file=sys.stderr)
            return 1
        lesson = await db.get_lesson_by_code(lesson_code)
        if lesson is None:
            print(f"Нет занятия {lesson_code}", file=sys.stderr)
            return 1
        marks = await db.list_attendance_for_lesson(lesson.id)
        by_id = {student.id: student for student in await db.list_students()}
        print(f"{lesson.code} {lesson.title}: {len(marks)}")
        for mark in marks:
            student = by_id.get(mark.student_id)
            name = student.full_name if student is not None else str(mark.student_id)
            print(f"{name}\t{mark.status}")
        return 0
    finally:
        await db.close()


async def cmd_grade_set(
    assessment_code: str, student_query: str, score: float, comment: str
) -> int:
    if score < 0 or score > 10:
        print("Балл должен быть от 0 до 10", file=sys.stderr)
        return 1
    settings = load_settings()
    db = await _with_db(settings.db_path)
    try:
        assessment = await db.get_assessment_by_code(assessment_code)
        if assessment is None:
            print(f"Нет элемента {assessment_code}", file=sys.stderr)
            return 1
        matched = resolve_student(student_query, await db.list_students())
        if isinstance(matched, MatchFailure):
            print(f"{matched.query}: {matched.reason}", file=sys.stderr)
            return 1
        report = await apply_grades(
            db, assessment, [(matched, score, comment)], dry_run=False
        )
        _print_write_report(report, dry_run=False)
        return 0
    finally:
        await db.close()


async def cmd_grade_import(assessment_code: str, path: Path, dry_run: bool) -> int:
    settings = load_settings()
    db = await _with_db(settings.db_path)
    try:
        assessment = await db.get_assessment_by_code(assessment_code)
        if assessment is None:
            print(f"Нет элемента {assessment_code}", file=sys.stderr)
            return 1
        key, rows = read_named_csv(path)
        students = await db.list_students()
        found, failed = resolve_all([row[key] for row in rows], students)
        if failed:
            _print_failures(failed)
            return 1
        assignments: list[tuple[Student, float, str]] = []
        for student, row in zip(found, rows, strict=True):
            try:
                score = float(row.get("score", ""))
            except ValueError:
                print("Не понял балл", file=sys.stderr)
                return 1
            if score < 0 or score > 10:
                print("Балл должен быть от 0 до 10", file=sys.stderr)
                return 1
            assignments.append((student, score, row.get("comment", "")))
        report = await apply_grades(db, assessment, assignments, dry_run=dry_run)
        _print_write_report(report, dry_run=dry_run)
        return 0
    finally:
        await db.close()


async def cmd_grade_show(assessment_code: str | None, student_query: str | None) -> int:
    settings = load_settings()
    db = await _with_db(settings.db_path)
    try:
        if student_query:
            matched = resolve_student(student_query, await db.list_students())
            if isinstance(matched, MatchFailure):
                print(f"{matched.query}: {matched.reason}", file=sys.stderr)
                return 1
            grades = await db.list_grades_for_student(matched.id)
            assessments = {item.id: item for item in await db.list_assessments()}
            if not grades:
                print("Оценок нет")
                return 0
            for grade in grades:
                item = assessments.get(grade.assessment_id)
                label = item.label if item is not None else str(grade.assessment_id)
                print(f"{label}\t{grade.score}")
            return 0
        if not assessment_code:
            print("Нужен --assessment или --student", file=sys.stderr)
            return 1
        assessment = await db.get_assessment_by_code(assessment_code)
        if assessment is None:
            print(f"Нет элемента {assessment_code}", file=sys.stderr)
            return 1
        grades = await db.list_grades_for_assessment(assessment.id)
        students = {student.id: student for student in await db.list_students()}
        print(f"{assessment.label}: {len(grades)}")
        for grade in grades:
            student = students.get(grade.student_id)
            name = student.full_name if student is not None else str(grade.student_id)
            print(f"{name}\t{grade.score}")
        return 0
    finally:
        await db.close()


async def cmd_gradebook(output: Path) -> int:
    settings = load_settings()
    course = load_course(DEFAULT_COURSE_PATH)
    db = await _with_db(settings.db_path)
    try:
        rows = await reports_for_students(db, course, now_ts())
        output.write_text(gradebook_csv(course, rows), encoding="utf-8")
        print(f"Записал {output}")
        return 0
    finally:
        await db.close()


async def cmd_set_seminar_group(
    student_query: str | None, group: str | None, from_roster: bool
) -> int:
    settings = load_settings()
    db = await _with_db(settings.db_path)
    try:
        if from_roster:
            updates = apply_roster_seminar_groups(
                await db.list_students(), settings.roster_path
            )
            for student, seminar_group in updates:
                await db.set_seminar_group(student.id, seminar_group)
            print(f"Обновлено: {len(updates)}")
            return 0
        if not student_query or not group:
            print("Нужны --student и --group либо --from-roster", file=sys.stderr)
            return 1
        try:
            seminar = canonical_seminar_group(group)
        except ValueError:
            print("Группа должна быть 261 или 262", file=sys.stderr)
            return 1
        matched = resolve_student(student_query, await db.list_students())
        if isinstance(matched, MatchFailure):
            print(f"{matched.query}: {matched.reason}", file=sys.stderr)
            return 1
        await db.set_seminar_group(matched.id, seminar)
        print(f"Семинарская группа {seminar}")
        return 0
    finally:
        await db.close()


async def _dispatch_attendance(args: argparse.Namespace) -> int:
    if args.att_command == "mark":
        return await cmd_attendance_mark(
            args.lesson, args.present, args.absent, args.excused
        )
    if args.att_command == "import":
        return await cmd_attendance_import(args.lesson, args.file, args.dry_run)
    if args.att_command == "show":
        return await cmd_attendance_show(args.lesson, args.student)
    return 2


async def _dispatch_grade(args: argparse.Namespace) -> int:
    if args.grade_command == "set":
        return await cmd_grade_set(
            args.assessment, args.student, args.score, args.comment
        )
    if args.grade_command == "import":
        return await cmd_grade_import(args.assessment, args.file, args.dry_run)
    if args.grade_command == "show":
        return await cmd_grade_show(args.assessment, args.student)
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hwbot", description="Бот сбора ДЗ БАЦРФ")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="Запустить Telegram-бота")
    sub.add_parser("import-roster", help="Залить список студентов из CSV")
    sub.add_parser("list-hw", help="Список ДЗ")
    sub.add_parser("overview", help="Что требует внимания прямо сейчас")

    broadcast = sub.add_parser("broadcast", help="Разослать текст зарегистрированным")
    broadcast.add_argument("--text", required=True)

    students_cmd = sub.add_parser("students", help="Список студентов")
    students_flags = students_cmd.add_mutually_exclusive_group()
    students_flags.add_argument("--registered", action="store_true")
    students_flags.add_argument("--missing", action="store_true")

    unbind = sub.add_parser("unbind", help="Отвязать Telegram от студента")
    unbind.add_argument("--student", required=True)

    status = sub.add_parser("status", help="Кто сдал")
    status.add_argument("--hw", type=int, required=True)

    export = sub.add_parser("export", help="Выгрузить CSV")
    export.add_argument("--hw", type=int, required=True)
    export.add_argument("--out", type=Path, default=None)

    seed = sub.add_parser("seed-course", help="Залить календарь и элементы контроля")
    seed.add_argument("--file", type=Path, default=DEFAULT_COURSE_PATH)
    seed.add_argument("--dry-run", action="store_true")

    attendance = sub.add_parser("attendance", help="Посещаемость")
    att_sub = attendance.add_subparsers(dest="att_command", required=True)
    att_mark = att_sub.add_parser("mark", help="Отметить студентов")
    att_mark.add_argument("--lesson", required=True)
    att_mark.add_argument("--present", default="")
    att_mark.add_argument("--absent", default="")
    att_mark.add_argument("--excused", default="")
    att_imp = att_sub.add_parser("import", help="Импорт посещаемости из CSV")
    att_imp.add_argument("--lesson", required=True)
    att_imp.add_argument("--file", type=Path, required=True)
    att_imp.add_argument("--dry-run", action="store_true")
    att_show = att_sub.add_parser("show", help="Показать посещаемость")
    att_show.add_argument("--lesson")
    att_show.add_argument("--student")

    grade = sub.add_parser("grade", help="Баллы")
    grade_sub = grade.add_subparsers(dest="grade_command", required=True)
    grade_set = grade_sub.add_parser("set", help="Поставить балл")
    grade_set.add_argument("--assessment", required=True)
    grade_set.add_argument("--student", required=True)
    grade_set.add_argument("--score", type=float, required=True)
    grade_set.add_argument("--comment", default="")
    grade_imp = grade_sub.add_parser("import", help="Импорт баллов из CSV")
    grade_imp.add_argument("--assessment", required=True)
    grade_imp.add_argument("--file", type=Path, required=True)
    grade_imp.add_argument("--dry-run", action="store_true")
    grade_show = grade_sub.add_parser("show", help="Показать баллы")
    grade_show.add_argument("--assessment")
    grade_show.add_argument("--student")

    gradebook = sub.add_parser("gradebook", help="Выгрузить ведомость")
    gradebook.add_argument("--out", type=Path, required=True)

    seminar = sub.add_parser("set-seminar-group", help="Семинарская группа")
    seminar.add_argument("--student")
    seminar.add_argument("--group")
    seminar.add_argument("--from-roster", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return asyncio.run(cmd_run())
    if args.command == "import-roster":
        return asyncio.run(cmd_import_roster())
    if args.command == "broadcast":
        return asyncio.run(cmd_broadcast(args.text))
    if args.command == "students":
        flag: bool | None = None
        if args.registered:
            flag = True
        elif args.missing:
            flag = False
        return asyncio.run(cmd_students(flag))
    if args.command == "unbind":
        return asyncio.run(cmd_unbind(args.student))
    if args.command == "list-hw":
        return asyncio.run(cmd_list_hw())
    if args.command == "overview":
        return asyncio.run(cmd_overview())
    if args.command == "status":
        return asyncio.run(cmd_status(args.hw))
    if args.command == "export":
        return asyncio.run(cmd_export(args.hw, args.out))
    if args.command == "seed-course":
        return asyncio.run(cmd_seed_course(args.file, args.dry_run))
    if args.command == "attendance":
        return asyncio.run(_dispatch_attendance(args))
    if args.command == "grade":
        return asyncio.run(_dispatch_grade(args))
    if args.command == "gradebook":
        return asyncio.run(cmd_gradebook(args.out))
    if args.command == "set-seminar-group":
        return asyncio.run(
            cmd_set_seminar_group(args.student, args.group, args.from_roster)
        )
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
