from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import aiosqlite

from hwbot.course import Assessment as CourseAssessment
from hwbot.course import Lesson as CourseLesson
from hwbot.errors import (
    AlreadyBoundError,
    DeadlineClosedError,
    HomeworkNotFoundError,
    StudentTakenError,
)
from hwbot.models import (
    Assessment,
    AttendanceMark,
    Grade,
    HomeworkStatusRow,
    Lesson,
    RosterRow,
    Student,
    Submission,
)
from hwbot.timeutil import is_deadline_open, now_ts

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL UNIQUE,
    group_code TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    telegram_id INTEGER UNIQUE,
    telegram_username TEXT,
    seminar_group TEXT
);

CREATE TABLE IF NOT EXISTS assessments (
    id            INTEGER PRIMARY KEY,
    code          TEXT NOT NULL UNIQUE,
    label         TEXT NOT NULL,
    title         TEXT NOT NULL,
    body          TEXT NOT NULL DEFAULT '',
    component     TEXT NOT NULL,
    weight_final  REAL NOT NULL,
    submit_via_bot INTEGER NOT NULL,
    issued_at     INTEGER,
    deadline_ts   INTEGER,
    accept_until_ts INTEGER,
    graded_on_ts  INTEGER,
    late_rule     TEXT NOT NULL DEFAULT 'none',
    blocking      INTEGER NOT NULL DEFAULT 0,
    active        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS lessons (
    id            INTEGER PRIMARY KEY,
    code          TEXT NOT NULL UNIQUE,
    kind          TEXT NOT NULL,
    seminar_group TEXT,
    topic         INTEGER NOT NULL,
    title         TEXT NOT NULL,
    starts_ts     INTEGER NOT NULL,
    ends_ts       INTEGER NOT NULL,
    room          TEXT,
    module        INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS attendance (
    student_id  INTEGER NOT NULL,
    lesson_id   INTEGER NOT NULL,
    status      TEXT NOT NULL,
    marked_at   INTEGER NOT NULL,
    PRIMARY KEY (student_id, lesson_id),
    FOREIGN KEY (student_id) REFERENCES students(id),
    FOREIGN KEY (lesson_id)  REFERENCES lessons(id)
);

CREATE TABLE IF NOT EXISTS grades (
    student_id    INTEGER NOT NULL,
    assessment_id INTEGER NOT NULL,
    score         REAL NOT NULL,
    comment       TEXT NOT NULL DEFAULT '',
    graded_at     INTEGER NOT NULL,
    PRIMARY KEY (student_id, assessment_id),
    FOREIGN KEY (student_id)    REFERENCES students(id),
    FOREIGN KEY (assessment_id) REFERENCES assessments(id)
);

CREATE TABLE IF NOT EXISTS submissions (
    id            INTEGER PRIMARY KEY,
    student_id    INTEGER NOT NULL,
    assessment_id INTEGER NOT NULL,
    payload       TEXT NOT NULL,
    submitted_at  INTEGER NOT NULL,
    FOREIGN KEY (student_id) REFERENCES students(id),
    FOREIGN KEY (assessment_id) REFERENCES assessments(id)
);

CREATE TABLE IF NOT EXISTS reminders_sent (
    assessment_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    window TEXT NOT NULL,
    sent_at INTEGER NOT NULL,
    PRIMARY KEY (assessment_id, student_id, window),
    FOREIGN KEY (assessment_id) REFERENCES assessments(id),
    FOREIGN KEY (student_id) REFERENCES students(id)
);

CREATE INDEX IF NOT EXISTS idx_attendance_lesson ON attendance(lesson_id);
CREATE INDEX IF NOT EXISTS idx_grades_assessment ON grades(assessment_id);
CREATE INDEX IF NOT EXISTS idx_submissions_pair  ON submissions(student_id, assessment_id);
"""


def _student_from_row(row: aiosqlite.Row) -> Student:
    seminar = row["seminar_group"] if "seminar_group" in row.keys() else None
    return Student(
        id=int(row["id"]),
        full_name=str(row["full_name"]),
        group_code=str(row["group_code"]),
        email=str(row["email"]),
        telegram_id=None if row["telegram_id"] is None else int(row["telegram_id"]),
        telegram_username=(
            None if row["telegram_username"] is None else str(row["telegram_username"])
        ),
        seminar_group=None if seminar is None else str(seminar),
    )


def _assessment_from_row(row: aiosqlite.Row) -> Assessment:
    return Assessment(
        id=int(row["id"]),
        code=str(row["code"]),
        label=str(row["label"]),
        title=str(row["title"]),
        body=str(row["body"]),
        component=str(row["component"]),
        weight_final=float(row["weight_final"]),
        submit_via_bot=bool(row["submit_via_bot"]),
        issued_at=None if row["issued_at"] is None else int(row["issued_at"]),
        deadline_ts=None if row["deadline_ts"] is None else int(row["deadline_ts"]),
        accept_until_ts=(
            None if row["accept_until_ts"] is None else int(row["accept_until_ts"])
        ),
        graded_on_ts=None if row["graded_on_ts"] is None else int(row["graded_on_ts"]),
        late_rule=str(row["late_rule"]),
        blocking=bool(row["blocking"]),
        active=bool(row["active"]),
    )


def _lesson_from_row(row: aiosqlite.Row) -> Lesson:
    return Lesson(
        id=int(row["id"]),
        code=str(row["code"]),
        kind=str(row["kind"]),
        seminar_group=None if row["seminar_group"] is None else str(row["seminar_group"]),
        topic=int(row["topic"]),
        title=str(row["title"]),
        starts_ts=int(row["starts_ts"]),
        ends_ts=int(row["ends_ts"]),
        room=None if row["room"] is None else str(row["room"]),
        module=int(row["module"]),
    )


def _submission_from_row(row: aiosqlite.Row) -> Submission:
    return Submission(
        id=int(row["id"]),
        student_id=int(row["student_id"]),
        assessment_id=int(row["assessment_id"]),
        payload=str(row["payload"]),
        submitted_at=int(row["submitted_at"]),
    )


def _grade_from_row(row: aiosqlite.Row) -> Grade:
    return Grade(
        student_id=int(row["student_id"]),
        assessment_id=int(row["assessment_id"]),
        score=float(row["score"]),
        comment=str(row["comment"]),
        graded_at=int(row["graded_at"]),
    )


def _attendance_from_row(row: aiosqlite.Row) -> AttendanceMark:
    return AttendanceMark(
        student_id=int(row["student_id"]),
        lesson_id=int(row["lesson_id"]),
        status=str(row["status"]),
        marked_at=int(row["marked_at"]),
    )


class Database:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._migrate_legacy_tables()
        await self._conn.executescript(SCHEMA)
        await self._ensure_seminar_group_column()
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def _require(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected")
        return self._conn

    async def _table_names(self) -> set[str]:
        conn = self._require()
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
        return {str(row["name"]) for row in await cursor.fetchall()}

    async def _columns(self, table: str) -> set[str]:
        conn = self._require()
        cursor = await conn.execute(f"PRAGMA table_info({table})")
        return {str(row["name"]) for row in await cursor.fetchall()}

    async def _count(self, table: str) -> int:
        conn = self._require()
        cursor = await conn.execute(f"SELECT COUNT(*) AS n FROM {table}")
        row = await cursor.fetchone()
        assert row is not None
        return int(row["n"])

    async def _migrate_legacy_tables(self) -> None:
        tables = await self._table_names()
        if "homeworks" in tables:
            if await self._count("homeworks") != 0:
                raise RuntimeError(
                    "Таблица homeworks не пуста: нужна ручная миграция, а не пересоздание"
                )
            await self._require().execute("DROP TABLE homeworks")
        if "submissions" in tables:
            columns = await self._columns("submissions")
            if "homework_id" in columns:
                if await self._count("submissions") != 0:
                    raise RuntimeError(
                        "Таблица submissions не пуста: нужна ручная миграция"
                    )
                await self._require().execute("DROP TABLE submissions")
        if "reminders_sent" in tables:
            columns = await self._columns("reminders_sent")
            if "homework_id" in columns:
                if await self._count("reminders_sent") != 0:
                    raise RuntimeError(
                        "Таблица reminders_sent не пуста: нужна ручная миграция"
                    )
                await self._require().execute("DROP TABLE reminders_sent")

    async def _ensure_seminar_group_column(self) -> None:
        columns = await self._columns("students")
        if "seminar_group" not in columns:
            await self._require().execute(
                "ALTER TABLE students ADD COLUMN seminar_group TEXT"
            )

    async def student_count(self) -> int:
        conn = self._require()
        cursor = await conn.execute("SELECT COUNT(*) AS n FROM students")
        row = await cursor.fetchone()
        assert row is not None
        return int(row["n"])

    async def seed_roster(self, rows: Sequence[RosterRow]) -> int:
        conn = self._require()
        inserted = 0
        for row in rows:
            cursor = await conn.execute(
                """
                INSERT INTO students (full_name, group_code, email, seminar_group)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(full_name) DO UPDATE SET
                    group_code = excluded.group_code,
                    email = excluded.email,
                    seminar_group = COALESCE(excluded.seminar_group, students.seminar_group)
                """,
                (row.full_name, row.group_code, row.email, row.seminar_group),
            )
            inserted += cursor.rowcount
        await conn.commit()
        return inserted

    async def list_students(self) -> list[Student]:
        conn = self._require()
        cursor = await conn.execute(
            "SELECT * FROM students ORDER BY group_code, full_name"
        )
        return [_student_from_row(row) for row in await cursor.fetchall()]

    async def students_in_groups(self, group_codes: Sequence[str]) -> list[Student]:
        if not group_codes:
            return []
        conn = self._require()
        placeholders = ",".join("?" * len(group_codes))
        cursor = await conn.execute(
            f"""
            SELECT * FROM students
            WHERE group_code IN ({placeholders})
            ORDER BY group_code, full_name
            """,
            tuple(group_codes),
        )
        return [_student_from_row(row) for row in await cursor.fetchall()]

    async def get_student(self, student_id: int) -> Student | None:
        conn = self._require()
        cursor = await conn.execute("SELECT * FROM students WHERE id = ?", (student_id,))
        row = await cursor.fetchone()
        return None if row is None else _student_from_row(row)

    async def get_student_by_telegram(self, telegram_id: int) -> Student | None:
        conn = self._require()
        cursor = await conn.execute(
            "SELECT * FROM students WHERE telegram_id = ?", (telegram_id,)
        )
        row = await cursor.fetchone()
        return None if row is None else _student_from_row(row)

    async def set_seminar_group(self, student_id: int, seminar_group: str) -> Student:
        conn = self._require()
        await conn.execute(
            "UPDATE students SET seminar_group = ? WHERE id = ?",
            (seminar_group, student_id),
        )
        await conn.commit()
        student = await self.get_student(student_id)
        if student is None:
            raise HomeworkNotFoundError("Студент не найден")
        return student

    async def bind_telegram(
        self,
        student_id: int,
        telegram_id: int,
        username: str | None,
    ) -> Student:
        conn = self._require()
        existing = await self.get_student_by_telegram(telegram_id)
        if existing is not None and existing.id != student_id:
            raise AlreadyBoundError("Этот Telegram уже привязан к другому студенту")
        target = await self.get_student(student_id)
        if target is None:
            raise HomeworkNotFoundError("Студент не найден")
        if target.telegram_id is not None and target.telegram_id != telegram_id:
            raise StudentTakenError("Этот человек уже зарегистрирован")
        await conn.execute(
            """
            UPDATE students
            SET telegram_id = ?, telegram_username = ?
            WHERE id = ?
            """,
            (telegram_id, username, student_id),
        )
        await conn.commit()
        bound = await self.get_student(student_id)
        assert bound is not None
        return bound

    async def upsert_lesson(self, lesson: CourseLesson) -> Literal["created", "updated", "unchanged"]:
        existing = await self.get_lesson_by_code(lesson.code)
        conn = self._require()
        values = (
            lesson.kind,
            lesson.seminar_group,
            lesson.topic,
            lesson.title,
            lesson.starts_ts,
            lesson.ends_ts,
            lesson.room,
            lesson.module,
            lesson.code,
        )
        if existing is None:
            await conn.execute(
                """
                INSERT INTO lessons
                    (kind, seminar_group, topic, title, starts_ts, ends_ts, room, module, code)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            await conn.commit()
            return "created"
        same = (
            existing.kind == lesson.kind
            and existing.seminar_group == lesson.seminar_group
            and existing.topic == lesson.topic
            and existing.title == lesson.title
            and existing.starts_ts == lesson.starts_ts
            and existing.ends_ts == lesson.ends_ts
            and existing.room == lesson.room
            and existing.module == lesson.module
        )
        if same:
            return "unchanged"
        await conn.execute(
            """
            UPDATE lessons SET
                kind = ?, seminar_group = ?, topic = ?, title = ?,
                starts_ts = ?, ends_ts = ?, room = ?, module = ?
            WHERE code = ?
            """,
            values,
        )
        await conn.commit()
        return "updated"

    async def upsert_assessment(
        self, assessment: CourseAssessment
    ) -> Literal["created", "updated", "unchanged"]:
        existing = await self.get_assessment_by_code(assessment.code)
        conn = self._require()
        values = (
            assessment.label,
            assessment.title,
            assessment.summary,
            assessment.component,
            assessment.weight_final,
            int(assessment.submit_via_bot),
            assessment.issued_at,
            assessment.deadline_ts,
            assessment.accept_until_ts,
            assessment.graded_on_ts,
            assessment.late_rule,
            int(assessment.blocking),
            assessment.code,
        )
        if existing is None:
            await conn.execute(
                """
                INSERT INTO assessments (
                    label, title, body, component, weight_final, submit_via_bot,
                    issued_at, deadline_ts, accept_until_ts, graded_on_ts,
                    late_rule, blocking, code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            await conn.commit()
            return "created"
        same = (
            existing.label == assessment.label
            and existing.title == assessment.title
            and existing.body == assessment.summary
            and existing.component == assessment.component
            and existing.weight_final == assessment.weight_final
            and existing.submit_via_bot == assessment.submit_via_bot
            and existing.issued_at == assessment.issued_at
            and existing.deadline_ts == assessment.deadline_ts
            and existing.accept_until_ts == assessment.accept_until_ts
            and existing.graded_on_ts == assessment.graded_on_ts
            and existing.late_rule == assessment.late_rule
            and existing.blocking == assessment.blocking
        )
        if same:
            return "unchanged"
        await conn.execute(
            """
            UPDATE assessments SET
                label = ?, title = ?, body = ?, component = ?, weight_final = ?,
                submit_via_bot = ?, issued_at = ?, deadline_ts = ?,
                accept_until_ts = ?, graded_on_ts = ?, late_rule = ?, blocking = ?
            WHERE code = ?
            """,
            values,
        )
        await conn.commit()
        return "updated"

    async def create_homework(
        self,
        title: str,
        body: str,
        deadline_ts: int,
        group_codes: Sequence[str],
        created_at: int | None = None,
    ) -> Assessment:
        _ = group_codes
        created = now_ts() if created_at is None else created_at
        code = f"adhoc-{created}"
        conn = self._require()
        accept_until_ts = deadline_ts + 7 * 86400
        cursor = await conn.execute(
            """
            INSERT INTO assessments (
                code, label, title, body, component, weight_final, submit_via_bot,
                issued_at, deadline_ts, accept_until_ts, late_rule, blocking, active
            ) VALUES (?, ?, ?, ?, 'homework', 0, 1, ?, ?, ?, 'homework', 0, 1)
            """,
            (code, title, title, body, created, deadline_ts, accept_until_ts),
        )
        await conn.commit()
        assessment_id = cursor.lastrowid
        assert assessment_id is not None
        assessment = await self.get_assessment(int(assessment_id))
        assert assessment is not None
        return assessment

    async def get_assessment(self, assessment_id: int) -> Assessment | None:
        conn = self._require()
        cursor = await conn.execute(
            "SELECT * FROM assessments WHERE id = ?", (assessment_id,)
        )
        row = await cursor.fetchone()
        return None if row is None else _assessment_from_row(row)

    async def get_assessment_by_code(self, code: str) -> Assessment | None:
        conn = self._require()
        cursor = await conn.execute("SELECT * FROM assessments WHERE code = ?", (code,))
        row = await cursor.fetchone()
        return None if row is None else _assessment_from_row(row)

    async def get_homework(self, homework_id: int) -> Assessment | None:
        return await self.get_assessment(homework_id)

    async def list_assessments(self, submit_via_bot: bool | None = None) -> list[Assessment]:
        conn = self._require()
        if submit_via_bot is None:
            cursor = await conn.execute(
                "SELECT * FROM assessments WHERE active = 1 ORDER BY deadline_ts, id"
            )
        else:
            cursor = await conn.execute(
                """
                SELECT * FROM assessments
                WHERE active = 1 AND submit_via_bot = ?
                ORDER BY deadline_ts, id
                """,
                (int(submit_via_bot),),
            )
        return [_assessment_from_row(row) for row in await cursor.fetchall()]

    async def list_homeworks(self, active_only: bool = False) -> list[Assessment]:
        _ = active_only
        return await self.list_assessments(submit_via_bot=True)

    async def homeworks_for_group(self, group_code: str) -> list[Assessment]:
        _ = group_code
        return await self.list_assessments(submit_via_bot=True)

    async def get_lesson(self, lesson_id: int) -> Lesson | None:
        conn = self._require()
        cursor = await conn.execute("SELECT * FROM lessons WHERE id = ?", (lesson_id,))
        row = await cursor.fetchone()
        return None if row is None else _lesson_from_row(row)

    async def get_lesson_by_code(self, code: str) -> Lesson | None:
        conn = self._require()
        cursor = await conn.execute("SELECT * FROM lessons WHERE code = ?", (code,))
        row = await cursor.fetchone()
        return None if row is None else _lesson_from_row(row)

    async def list_lessons(self) -> list[Lesson]:
        conn = self._require()
        cursor = await conn.execute("SELECT * FROM lessons ORDER BY starts_ts, id")
        return [_lesson_from_row(row) for row in await cursor.fetchall()]

    async def count_lessons(self) -> int:
        conn = self._require()
        cursor = await conn.execute("SELECT COUNT(*) AS n FROM lessons")
        row = await cursor.fetchone()
        assert row is not None
        return int(row["n"])

    async def count_assessments(self) -> int:
        conn = self._require()
        cursor = await conn.execute("SELECT COUNT(*) AS n FROM assessments")
        row = await cursor.fetchone()
        assert row is not None
        return int(row["n"])

    async def latest_submission(
        self, student_id: int, assessment_id: int
    ) -> Submission | None:
        conn = self._require()
        cursor = await conn.execute(
            """
            SELECT * FROM submissions
            WHERE student_id = ? AND assessment_id = ?
            ORDER BY submitted_at DESC, id DESC
            LIMIT 1
            """,
            (student_id, assessment_id),
        )
        row = await cursor.fetchone()
        return None if row is None else _submission_from_row(row)

    async def list_student_submissions(
        self, student_id: int
    ) -> list[tuple[Assessment, Submission]]:
        conn = self._require()
        cursor = await conn.execute(
            """
            SELECT s.*, a.code, a.label, a.title, a.body, a.component, a.weight_final,
                   a.submit_via_bot, a.issued_at, a.deadline_ts, a.accept_until_ts,
                   a.graded_on_ts, a.late_rule, a.blocking, a.active
            FROM submissions s
            JOIN assessments a ON a.id = s.assessment_id
            WHERE s.student_id = ?
            ORDER BY s.submitted_at DESC, s.id DESC
            """,
            (student_id,),
        )
        latest: dict[int, tuple[Assessment, Submission]] = {}
        for row in await cursor.fetchall():
            submission = _submission_from_row(row)
            if submission.assessment_id in latest:
                continue
            assessment = Assessment(
                id=submission.assessment_id,
                code=str(row["code"]),
                label=str(row["label"]),
                title=str(row["title"]),
                body=str(row["body"]),
                component=str(row["component"]),
                weight_final=float(row["weight_final"]),
                submit_via_bot=bool(row["submit_via_bot"]),
                issued_at=None if row["issued_at"] is None else int(row["issued_at"]),
                deadline_ts=None if row["deadline_ts"] is None else int(row["deadline_ts"]),
                accept_until_ts=(
                    None if row["accept_until_ts"] is None else int(row["accept_until_ts"])
                ),
                graded_on_ts=(
                    None if row["graded_on_ts"] is None else int(row["graded_on_ts"])
                ),
                late_rule=str(row["late_rule"]),
                blocking=bool(row["blocking"]),
                active=bool(row["active"]),
            )
            latest[submission.assessment_id] = (assessment, submission)
        return list(latest.values())

    async def add_submission(
        self,
        student_id: int,
        assessment_id: int,
        payload: str,
        submitted_at: int | None = None,
    ) -> Submission:
        assessment = await self.get_assessment(assessment_id)
        if assessment is None or not assessment.active:
            raise HomeworkNotFoundError("Задание не найдено")
        moment = now_ts() if submitted_at is None else submitted_at
        close_ts = assessment.accept_until_ts
        if close_ts is not None and not is_deadline_open(close_ts, moment):
            raise DeadlineClosedError("Приём уже закрыт")
        student = await self.get_student(student_id)
        if student is None:
            raise HomeworkNotFoundError("Студент не найден")
        text = payload.strip()
        if not text:
            raise ValueError("Пустая сдача")
        conn = self._require()
        await conn.execute(
            """
            INSERT INTO submissions (student_id, assessment_id, payload, submitted_at)
            VALUES (?, ?, ?, ?)
            """,
            (student_id, assessment_id, text, moment),
        )
        await conn.commit()
        saved = await self.latest_submission(student_id, assessment_id)
        assert saved is not None
        return saved

    async def homework_status(self, homework_id: int) -> list[HomeworkStatusRow]:
        assessment = await self.get_assessment(homework_id)
        if assessment is None:
            raise HomeworkNotFoundError("Задание не найдено")
        students = await self.list_students()
        moment = now_ts()
        rows: list[HomeworkStatusRow] = []
        for student in students:
            submission = await self.latest_submission(student.id, homework_id)
            rows.append(
                HomeworkStatusRow(
                    student=student,
                    submission=submission,
                    accept_until_ts=assessment.accept_until_ts,
                    now_ts=moment,
                )
            )
        return rows

    async def mark_attendance(
        self,
        student_id: int,
        lesson_id: int,
        status: str,
        marked_at: int | None = None,
    ) -> Literal["created", "updated"]:
        conn = self._require()
        existing = await self.get_attendance(student_id, lesson_id)
        moment = now_ts() if marked_at is None else marked_at
        await conn.execute(
            """
            INSERT INTO attendance (student_id, lesson_id, status, marked_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(student_id, lesson_id) DO UPDATE SET
                status = excluded.status,
                marked_at = excluded.marked_at
            """,
            (student_id, lesson_id, status, moment),
        )
        await conn.commit()
        return "updated" if existing is not None else "created"

    async def get_attendance(
        self, student_id: int, lesson_id: int
    ) -> AttendanceMark | None:
        conn = self._require()
        cursor = await conn.execute(
            """
            SELECT * FROM attendance
            WHERE student_id = ? AND lesson_id = ?
            """,
            (student_id, lesson_id),
        )
        row = await cursor.fetchone()
        return None if row is None else _attendance_from_row(row)

    async def list_attendance_for_lesson(self, lesson_id: int) -> list[AttendanceMark]:
        conn = self._require()
        cursor = await conn.execute(
            "SELECT * FROM attendance WHERE lesson_id = ? ORDER BY student_id",
            (lesson_id,),
        )
        return [_attendance_from_row(row) for row in await cursor.fetchall()]

    async def list_attendance_for_student(self, student_id: int) -> list[AttendanceMark]:
        conn = self._require()
        cursor = await conn.execute(
            "SELECT * FROM attendance WHERE student_id = ? ORDER BY lesson_id",
            (student_id,),
        )
        return [_attendance_from_row(row) for row in await cursor.fetchall()]

    async def held_lesson_ids(self) -> set[int]:
        conn = self._require()
        cursor = await conn.execute("SELECT DISTINCT lesson_id FROM attendance")
        return {int(row["lesson_id"]) for row in await cursor.fetchall()}

    async def set_grade(
        self,
        student_id: int,
        assessment_id: int,
        score: float,
        comment: str = "",
        graded_at: int | None = None,
    ) -> Literal["created", "updated"]:
        existing = await self.get_grade(student_id, assessment_id)
        conn = self._require()
        moment = now_ts() if graded_at is None else graded_at
        await conn.execute(
            """
            INSERT INTO grades (student_id, assessment_id, score, comment, graded_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(student_id, assessment_id) DO UPDATE SET
                score = excluded.score,
                comment = excluded.comment,
                graded_at = excluded.graded_at
            """,
            (student_id, assessment_id, score, comment, moment),
        )
        await conn.commit()
        return "updated" if existing is not None else "created"

    async def get_grade(self, student_id: int, assessment_id: int) -> Grade | None:
        conn = self._require()
        cursor = await conn.execute(
            """
            SELECT * FROM grades
            WHERE student_id = ? AND assessment_id = ?
            """,
            (student_id, assessment_id),
        )
        row = await cursor.fetchone()
        return None if row is None else _grade_from_row(row)

    async def list_grades_for_assessment(self, assessment_id: int) -> list[Grade]:
        conn = self._require()
        cursor = await conn.execute(
            "SELECT * FROM grades WHERE assessment_id = ? ORDER BY student_id",
            (assessment_id,),
        )
        return [_grade_from_row(row) for row in await cursor.fetchall()]

    async def list_grades_for_student(self, student_id: int) -> list[Grade]:
        conn = self._require()
        cursor = await conn.execute(
            "SELECT * FROM grades WHERE student_id = ? ORDER BY assessment_id",
            (student_id,),
        )
        return [_grade_from_row(row) for row in await cursor.fetchall()]

    async def reminder_was_sent(
        self, assessment_id: int, student_id: int, window: str
    ) -> bool:
        conn = self._require()
        cursor = await conn.execute(
            """
            SELECT 1 FROM reminders_sent
            WHERE assessment_id = ? AND student_id = ? AND window = ?
            """,
            (assessment_id, student_id, window),
        )
        return await cursor.fetchone() is not None

    async def mark_reminder_sent(
        self, assessment_id: int, student_id: int, window: str, sent_at: int | None = None
    ) -> None:
        conn = self._require()
        await conn.execute(
            """
            INSERT OR IGNORE INTO reminders_sent (assessment_id, student_id, window, sent_at)
            VALUES (?, ?, ?, ?)
            """,
            (assessment_id, student_id, window, now_ts() if sent_at is None else sent_at),
        )
        await conn.commit()

    async def sent_reminder_keys(self) -> set[tuple[int, int, str]]:
        conn = self._require()
        cursor = await conn.execute(
            "SELECT assessment_id, student_id, window FROM reminders_sent"
        )
        return {
            (int(row["assessment_id"]), int(row["student_id"]), str(row["window"]))
            for row in await cursor.fetchall()
        }
