from __future__ import annotations

import csv
import io

from hwbot.course import Course
from hwbot.grading import GradeReport, final_score, round_half_up
from hwbot.models import Assessment, HomeworkStatusRow, Student
from hwbot.timeutil import format_dt


def status_csv(assessment: Assessment, rows: list[HomeworkStatusRow]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "full_name",
            "group_code",
            "telegram_username",
            "telegram_id",
            "submitted_at",
            "payload",
            "status",
        ]
    )
    for row in rows:
        submission = row.submission
        writer.writerow(
            [
                row.student.full_name,
                row.student.group_code,
                row.student.telegram_username or "",
                row.student.telegram_id or "",
                format_dt(submission.submitted_at) if submission else "",
                submission.payload if submission else "",
                row.status_label,
            ]
        )
    _ = assessment
    return buffer.getvalue()


def format_status_text(assessment: Assessment, rows: list[HomeworkStatusRow]) -> str:
    done = [row for row in rows if row.submission is not None]
    missing = [row for row in rows if row.submission is None]
    lines = [
        f"ДЗ #{assessment.id} {assessment.title}",
        f"Сдали: {len(done)} / {len(rows)}",
        f"Не сдали: {len(missing)}",
    ]
    if missing:
        lines.append("")
        lines.append("Не сдали:")
        for row in missing:
            lines.append(f"— {row.student.full_name} ({row.student.group_code})")
    return "\n".join(lines)


def _num(value: float | None, digits: int = 1) -> str:
    if value is None:
        return ""
    rounded = round_half_up(value, digits)
    if digits == 0:
        return str(int(rounded))
    text = f"{rounded:.{digits}f}"
    return text.replace(".", ",")


def gradebook_csv(
    course: Course, rows: list[tuple[Student, GradeReport]]
) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    headers = ["ФИО", "Учебная группа", "Семинарская группа"]
    for assessment in course.assessments:
        headers.extend(
            [
                assessment.label,
                f"{assessment.label} просрочка",
                f"{assessment.label} потолок",
            ]
        )
    for component in course.components:
        headers.append(component.title)
    headers.extend(["Идёшь на", "В кармане", "Если ничего", "В ведомость"])
    writer.writerow(headers)
    for student, report in rows:
        items = {item.code: item for item in report.items}
        line: list[str] = [
            student.full_name,
            student.group_code,
            student.seminar_group or "",
        ]
        for assessment in course.assessments:
            item = items.get(assessment.code)
            if item is None:
                line.extend(["", "", ""])
                continue
            late = "" if item.days_late == 0 else str(item.days_late)
            cap = "" if item.cap is None else _num(item.cap)
            line.extend([_num(item.applied_score), late, cap])
        scores = {line_item.key: line_item.score_now for line_item in report.components}
        for component in course.components:
            line.append(_num(scores.get(component.key)))
        official = ""
        exam_item = items.get("exam")
        if (
            exam_item is not None
            and exam_item.applied_score is not None
            and report.in_pocket is not None
        ):
            final = final_score(report.in_pocket, exam_item.applied_score, course)
            if final is not None:
                official = _num(round_half_up(final, 0), 0)
        line.extend(
            [
                _num(report.heading_to),
                _num(report.in_pocket),
                _num(report.if_nothing),
                official,
            ]
        )
        writer.writerow(line)
    return buffer.getvalue()
