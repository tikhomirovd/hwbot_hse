from __future__ import annotations

import csv
import io

from hwbot.models import Assessment, HomeworkStatusRow
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
