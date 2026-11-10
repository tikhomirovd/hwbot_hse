from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import asyncio
import sqlite3

import aiosqlite

from hwbot.course import Assessment as CourseAssessment
from hwbot.course import Lesson as CourseLesson
from hwbot.errors import (
    AlreadyBoundError,
    AlreadyInTeamError,
    DeadlineClosedError,
    HomeworkNotFoundError,
    InviteGoneError,
    NotInTeamError,
    NotIssuedError,
    StudentTakenError,
    TeamFullError,
    TeamNotFoundError,
)
from hwbot.models import (
    Assessment,
    AttendanceMark,
    DbCredential,
    Grade,
    HomeworkStatusRow,
    Invite,
    Lesson,
    RosterRow,
    Student,
    Submission,
    Team,
    clean_team_name,
)
from hwbot.reconcile import assessment_changes, lesson_changes
from hwbot.timeutil import is_deadline_open, now_ts

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL UNIQUE,
    group_code TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    telegram_id INTEGER UNIQUE,
    telegram_username TEXT,
    seminar_group TEXT,
    registered_at INTEGER,
    db_login TEXT,
    db_password TEXT
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

CREATE TABLE IF NOT EXISTS teams (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT,
    created_at INTEGER NOT NULL
);

-- student_id — первичный ключ: в двух командах сразу студент не окажется,
-- и держит это база, а не проверка в коде.
CREATE TABLE IF NOT EXISTS team_members (
    student_id INTEGER PRIMARY KEY,
    team_id    INTEGER NOT NULL,
    joined_at  INTEGER NOT NULL,
    FOREIGN KEY (student_id) REFERENCES students(id),
    FOREIGN KEY (team_id)    REFERENCES teams(id)
);

-- Только приглашения без ответа: ответ, отзыв и погасание удаляют строку.
-- AUTOINCREMENT не отдаёт новому приглашению номер удалённого, поэтому
-- старая кнопка «Принять» не попадёт в чужое приглашение.
CREATE TABLE IF NOT EXISTS team_invites (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    inviter_id INTEGER NOT NULL,
    invitee_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE (inviter_id, invitee_id),
    FOREIGN KEY (inviter_id) REFERENCES students(id),
    FOREIGN KEY (invitee_id) REFERENCES students(id)
);

CREATE INDEX IF NOT EXISTS idx_attendance_lesson ON attendance(lesson_id);
CREATE INDEX IF NOT EXISTS idx_grades_assessment ON grades(assessment_id);
CREATE INDEX IF NOT EXISTS idx_submissions_pair  ON submissions(student_id, assessment_id);
CREATE INDEX IF NOT EXISTS idx_team_members_team ON team_members(team_id);
CREATE INDEX IF NOT EXISTS idx_team_invites_invitee ON team_invites(invitee_id);
"""

_TEAM_SELECT = """
    SELECT t.id AS team_id, t.name AS team_name, s.*
    FROM teams t
    JOIN team_members m ON m.team_id = t.id
    JOIN students s ON s.id = m.student_id
"""

_INVITE_COLUMNS = "id, inviter_id, invitee_id, created_at"

def _invite_from_row(row: aiosqlite.Row) -> Invite:
    return Invite(
        id=int(row["id"]),
        inviter_id=int(row["inviter_id"]),
        invitee_id=int(row["invitee_id"]),
        created_at=int(row["created_at"]),
    )


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
        registered_at=_optional_int(row, "registered_at"),
        db_login=_optional_str(row, "db_login"),
        db_password=_optional_str(row, "db_password"),
    )


def _optional_str(row: aiosqlite.Row, key: str) -> str | None:
    if key not in row.keys() or row[key] is None:
        return None
    return str(row[key])


def _optional_int(row: aiosqlite.Row, key: str) -> int | None:
    if key not in row.keys() or row[key] is None:
        return None
    return int(row[key])


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
        # ponytail: один замок на все записи о командах — между await-ами
        # обработчики перемежаются, и «проверил, потом записал» иначе гоняется.
        # Если команд станет много, хватит замка на команду.
        self._team_lock = asyncio.Lock()

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._migrate_legacy_tables()
        await self._conn.executescript(SCHEMA)
        await self._ensure_seminar_group_column()
        await self._ensure_registered_at_column()
        await self._ensure_db_credential_columns()
        await self._normalize_seminar_groups()
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def _require(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected")
        return self._conn

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[None]:
        conn = self._require()
        await conn.execute("BEGIN")
        try:
            yield
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise

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

    async def _ensure_registered_at_column(self) -> None:
        columns = await self._columns("students")
        if "registered_at" not in columns:
            await self._require().execute(
                "ALTER TABLE students ADD COLUMN registered_at INTEGER"
            )

    async def _ensure_db_credential_columns(self) -> None:
        columns = await self._columns("students")
        for column in ("db_login", "db_password"):
            if column not in columns:
                await self._require().execute(
                    f"ALTER TABLE students ADD COLUMN {column} TEXT"
                )

    async def _normalize_seminar_groups(self) -> None:
        conn = self._require()
        tables = await self._table_names()
        if "students" in tables:
            await conn.execute(
                """
                UPDATE students
                SET seminar_group = CASE
                    WHEN seminar_group IN ('А', 'A', 'а') THEN '261'
                    WHEN seminar_group IN ('Б', 'B', 'б') THEN '262'
                    WHEN group_code LIKE '%261' THEN '261'
                    WHEN group_code LIKE '%262' THEN '262'
                    ELSE seminar_group
                END
                WHERE seminar_group IS NULL
                   OR seminar_group IN ('А', 'Б', 'A', 'B', 'а', 'б')
                """
            )
        if "lessons" in tables:
            await conn.execute(
                "UPDATE lessons SET seminar_group = '261' "
                "WHERE seminar_group IN ('А', 'A', 'а')"
            )
            await conn.execute(
                "UPDATE lessons SET seminar_group = '262' "
                "WHERE seminar_group IN ('Б', 'B', 'б')"
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

    async def set_db_credentials(
        self, credentials: Sequence[DbCredential]
    ) -> tuple[int, list[str]]:
        """Разложить логины и пароли учебной базы по студентам.

        Связь — по локальной части почты: логин в PostgreSQL сделан из неё.
        Возвращает число обновлённых строк и логины, которым не нашлось
        студента, — их надо разобрать руками, а не проглотить молча.
        """
        conn = self._require()
        updated = 0
        orphans: list[str] = []
        for credential in credentials:
            cursor = await conn.execute(
                """
                UPDATE students
                SET db_login = ?, db_password = ?
                WHERE substr(email, 1, instr(email, '@') - 1) = ?
                """,
                (credential.login, credential.password, credential.login),
            )
            if cursor.rowcount:
                updated += cursor.rowcount
            else:
                orphans.append(credential.login)
        await conn.commit()
        return updated, orphans

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
        try:
            await conn.execute(
                """
                UPDATE students
                SET telegram_id = ?, telegram_username = ?,
                    registered_at = COALESCE(registered_at, ?)
                WHERE id = ?
                """,
                (telegram_id, username, now_ts(), student_id),
            )
            await conn.commit()
        except sqlite3.IntegrityError as exc:
            existing = await self.get_student_by_telegram(telegram_id)
            if existing is not None and existing.id != student_id:
                raise AlreadyBoundError(
                    "Этот Telegram уже привязан к другому студенту"
                ) from exc
            target = await self.get_student(student_id)
            if (
                target is not None
                and target.telegram_id is not None
                and target.telegram_id != telegram_id
            ):
                raise StudentTakenError("Этот человек уже зарегистрирован") from exc
            raise
        bound = await self.get_student(student_id)
        assert bound is not None
        return bound

    async def unbind_telegram(self, student_id: int) -> Student:
        conn = self._require()
        target = await self.get_student(student_id)
        if target is None:
            raise HomeworkNotFoundError("Студент не найден")
        await conn.execute(
            """
            UPDATE students
            SET telegram_id = NULL, telegram_username = NULL
            WHERE id = ?
            """,
            (student_id,),
        )
        await conn.commit()
        student = await self.get_student(student_id)
        if student is None:
            raise HomeworkNotFoundError("Студент не найден")
        return student

    async def write_lesson(self, lesson: CourseLesson) -> None:
        conn = self._require()
        await conn.execute(
            """
            INSERT INTO lessons
                (code, kind, seminar_group, topic, title, starts_ts, ends_ts, room, module)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                kind = excluded.kind,
                seminar_group = excluded.seminar_group,
                topic = excluded.topic,
                title = excluded.title,
                starts_ts = excluded.starts_ts,
                ends_ts = excluded.ends_ts,
                room = excluded.room,
                module = excluded.module
            """,
            (
                lesson.code,
                lesson.kind,
                lesson.seminar_group,
                lesson.topic,
                lesson.title,
                lesson.starts_ts,
                lesson.ends_ts,
                lesson.room,
                lesson.module,
            ),
        )

    async def upsert_lesson(
        self, lesson: CourseLesson
    ) -> Literal["created", "updated", "unchanged"]:
        existing = await self.get_lesson_by_code(lesson.code)
        if existing is not None and not lesson_changes(existing, lesson):
            return "unchanged"
        await self.write_lesson(lesson)
        await self._require().commit()
        return "created" if existing is None else "updated"

    async def write_assessment(self, assessment: CourseAssessment) -> None:
        conn = self._require()
        await conn.execute(
            """
            INSERT INTO assessments (
                code, label, title, body, component, weight_final, submit_via_bot,
                issued_at, deadline_ts, accept_until_ts, graded_on_ts,
                late_rule, blocking, active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(code) DO UPDATE SET
                label = excluded.label,
                title = excluded.title,
                body = excluded.body,
                component = excluded.component,
                weight_final = excluded.weight_final,
                submit_via_bot = excluded.submit_via_bot,
                issued_at = excluded.issued_at,
                deadline_ts = excluded.deadline_ts,
                accept_until_ts = excluded.accept_until_ts,
                graded_on_ts = excluded.graded_on_ts,
                late_rule = excluded.late_rule,
                blocking = excluded.blocking,
                active = 1
            """,
            (
                assessment.code,
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
            ),
        )

    async def upsert_assessment(
        self, assessment: CourseAssessment
    ) -> Literal["created", "updated", "unchanged"]:
        existing = await self.get_assessment_by_code(assessment.code)
        if existing is not None and not assessment_changes(existing, assessment):
            return "unchanged"
        await self.write_assessment(assessment)
        await self._require().commit()
        return "created" if existing is None else "updated"

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

    async def all_assessments(self) -> list[Assessment]:
        conn = self._require()
        cursor = await conn.execute("SELECT * FROM assessments ORDER BY id")
        return [_assessment_from_row(row) for row in await cursor.fetchall()]

    async def list_homeworks(self, active_only: bool = False) -> list[Assessment]:
        _ = active_only
        return await self.list_assessments(submit_via_bot=True)

    async def submittable_assessments(self) -> list[Assessment]:
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

    async def latest_submissions_map(self) -> dict[tuple[int, int], Submission]:
        conn = self._require()
        cursor = await conn.execute(
            """
            SELECT id, student_id, assessment_id, payload, submitted_at
            FROM (
                SELECT s.*, ROW_NUMBER() OVER (
                    PARTITION BY s.student_id, s.assessment_id
                    ORDER BY s.submitted_at DESC, s.id DESC
                ) AS rn
                FROM submissions s
            )
            WHERE rn = 1
            """
        )
        result: dict[tuple[int, int], Submission] = {}
        for row in await cursor.fetchall():
            submission = _submission_from_row(row)
            result[(submission.assessment_id, submission.student_id)] = submission
        return result

    async def grades_map(self) -> dict[tuple[int, int], Grade]:
        conn = self._require()
        cursor = await conn.execute("SELECT * FROM grades")
        result: dict[tuple[int, int], Grade] = {}
        for row in await cursor.fetchall():
            grade = _grade_from_row(row)
            result[(grade.assessment_id, grade.student_id)] = grade
        return result

    async def marked_student_ids_by_lesson(self) -> dict[int, set[int]]:
        conn = self._require()
        cursor = await conn.execute("SELECT lesson_id, student_id FROM attendance")
        result: dict[int, set[int]] = {}
        for row in await cursor.fetchall():
            result.setdefault(int(row["lesson_id"]), set()).add(int(row["student_id"]))
        return result

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
        if assessment.issued_at is not None and moment < assessment.issued_at:
            raise NotIssuedError(assessment.issued_at)
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

    # Команды итогового проекта. Соединение одно на весь бот, поэтому здесь
    # нет rollback(): он снёс бы и чужую незакоммиченную запись. Ошибку
    # исправляем удалением своего, неудачную вставку закрываем commit().
    # Отправка, ответ на приглашение и выход идут под self._team_lock; внутри
    # него зовём только методы без замка: он не реентерабельный.

    async def _teams_where(self, where: str, params: tuple[object, ...]) -> list[Team]:
        conn = self._require()
        cursor = await conn.execute(
            f"{_TEAM_SELECT} {where} ORDER BY t.id, m.joined_at, m.student_id",
            params,
        )
        found: dict[int, tuple[str | None, list[Student]]] = {}
        for row in await cursor.fetchall():
            name = None if row["team_name"] is None else str(row["team_name"])
            entry = found.setdefault(int(row["team_id"]), (name, []))
            entry[1].append(_student_from_row(row))
        return [
            Team(id=team_id, name=name, members=tuple(members))
            for team_id, (name, members) in found.items()
        ]

    async def list_teams(self) -> list[Team]:
        return await self._teams_where("", ())

    async def get_team(self, team_id: int) -> Team | None:
        teams = await self._teams_where("WHERE t.id = ?", (team_id,))
        return teams[0] if teams else None

    async def team_of_student(self, student_id: int) -> Team | None:
        teams = await self._teams_where(
            "WHERE t.id = (SELECT team_id FROM team_members WHERE student_id = ?)",
            (student_id,),
        )
        return teams[0] if teams else None

    async def create_team(
        self, student_ids: Sequence[int], *, max_size: int, now: int | None = None
    ) -> Team:
        if not student_ids or len(set(student_ids)) != len(student_ids):
            raise ValueError("Нужны разные студенты, хотя бы один")
        if len(student_ids) > max_size:
            raise TeamFullError("В команде больше людей, чем можно")
        conn = self._require()
        moment = now_ts() if now is None else now
        cursor = await conn.execute("INSERT INTO teams (created_at) VALUES (?)", (moment,))
        assert cursor.lastrowid is not None
        team_id = int(cursor.lastrowid)
        try:
            for student_id in student_ids:
                await conn.execute(
                    "INSERT INTO team_members (student_id, team_id, joined_at) "
                    "VALUES (?, ?, ?)",
                    (student_id, team_id, moment),
                )
        except sqlite3.IntegrityError as exc:
            await conn.execute("DELETE FROM team_members WHERE team_id = ?", (team_id,))
            await conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))
            await conn.commit()
            raise AlreadyInTeamError("Студент уже в команде") from exc
        await conn.commit()
        team = await self.get_team(team_id)
        assert team is not None
        return team

    async def join_team(
        self, team_id: int, student_id: int, *, max_size: int, now: int | None = None
    ) -> Team:
        conn = self._require()
        moment = now_ts() if now is None else now
        # Проверка места и вставка — один запрос: двое не займут последнее место разом.
        try:
            cursor = await conn.execute(
                """
                INSERT INTO team_members (student_id, team_id, joined_at)
                SELECT ?, id, ? FROM teams
                WHERE id = ?
                  AND (SELECT COUNT(*) FROM team_members WHERE team_id = teams.id) < ?
                """,
                (student_id, moment, team_id, max_size),
            )
        except sqlite3.IntegrityError as exc:
            await conn.commit()
            raise AlreadyInTeamError("Студент уже в команде") from exc
        await conn.commit()
        team = await self.get_team(team_id)
        if team is None:
            raise TeamNotFoundError("Команды нет")
        if cursor.rowcount == 0:
            raise TeamFullError("Команда заполнена")
        return team

    async def leave_team(
        self, student_id: int, *, dissolve: bool = False
    ) -> tuple[Team, list[Invite]]:
        """Убрать студента из команды: команда, какой была до выхода, и погасшие приглашения.

        dissolve=True — так выходят через бота: от пары не остаётся команды из
        одного, второй тоже оказывается без команды. CLI зовёт без него, чтобы
        преподаватель мог убрать одного и добавить другого.
        """
        async with self._team_lock:
            team = await self.team_of_student(student_id)
            if team is None:
                raise NotInTeamError("Студент не в команде")
            conn = self._require()
            leaving = [student_id]
            remaining = [member.id for member in team.members if member.id != student_id]
            if dissolve and len(remaining) == 1:
                leaving += remaining
            for leaver in leaving:
                await conn.execute("DELETE FROM team_members WHERE student_id = ?", (leaver,))
            # Пустая команда удаляется. Условие — в запросе: если кто-то успел
            # вступить, команда останется.
            await conn.execute(
                """
                DELETE FROM teams
                WHERE id = ?
                  AND NOT EXISTS (SELECT 1 FROM team_members WHERE team_id = teams.id)
                """,
                (team.id,),
            )
            # Ушедший больше не зовёт людей в прежнюю команду.
            dropped = await self._drop_invites([student_id])
            await conn.commit()
        return team, dropped

    async def rename_team(self, team_id: int, raw: str) -> str | None:
        """Записать название (пустое — убрать) и вернуть то, что записано."""
        name = clean_team_name(raw)
        conn = self._require()
        await conn.execute("UPDATE teams SET name = ? WHERE id = ?", (name, team_id))
        await conn.commit()
        return name

    async def list_invites(self) -> list[Invite]:
        conn = self._require()
        cursor = await conn.execute(f"SELECT {_INVITE_COLUMNS} FROM team_invites ORDER BY id")
        return [_invite_from_row(row) for row in await cursor.fetchall()]

    async def send_invite(
        self, inviter_id: int, invitee_id: int, *, max_size: int, now: int | None = None
    ) -> Invite | None:
        """Позвать к себе в команду. None — такое приглашение уже ждёт ответа.

        Ждущих ответа у команды (у одиночки — у него самого) не больше, чем
        свободных мест: иначе на последнее место согласятся двое.
        """
        if inviter_id == invitee_id:
            raise ValueError("Себя не позвать")
        async with self._team_lock:
            conn = self._require()
            if await self.team_of_student(invitee_id) is not None:
                raise AlreadyInTeamError("Студент уже в команде")
            cursor = await conn.execute(
                "SELECT 1 FROM team_invites WHERE inviter_id = ? AND invitee_id = ?",
                (inviter_id, invitee_id),
            )
            if await cursor.fetchone() is not None:
                return None
            team = await self.team_of_student(inviter_id)
            members = [member.id for member in team.members] if team else [inviter_id]
            marks = ", ".join("?" for _ in members)
            cursor = await conn.execute(
                f"SELECT COUNT(*) FROM team_invites WHERE inviter_id IN ({marks})", members
            )
            row = await cursor.fetchone()
            pending = 0 if row is None else int(row[0])
            if len(members) + pending >= max_size:
                raise TeamFullError("Свободных мест не осталось")
            moment = now_ts() if now is None else now
            cursor = await conn.execute(
                "INSERT INTO team_invites (inviter_id, invitee_id, created_at) VALUES (?, ?, ?)",
                (inviter_id, invitee_id, moment),
            )
            await conn.commit()
        assert cursor.lastrowid is not None
        return Invite(
            id=int(cursor.lastrowid),
            inviter_id=inviter_id,
            invitee_id=invitee_id,
            created_at=moment,
        )

    async def take_invite(
        self, invite_id: int, student_id: int, *, as_inviter: bool
    ) -> Invite | None:
        """Забрать приглашение: отказ — адресатом, отзыв — автором.

        Роль — в условии запроса: чужое приглашение так не взять, а второе
        нажатие получает None.
        """
        column = "inviter_id" if as_inviter else "invitee_id"
        async with self._team_lock:
            conn = self._require()
            cursor = await conn.execute(
                f"DELETE FROM team_invites WHERE id = ? AND {column} = ? "
                f"RETURNING {_INVITE_COLUMNS}",
                (invite_id, student_id),
            )
            rows = list(await cursor.fetchall())
            await conn.commit()
        return _invite_from_row(rows[0]) if rows else None

    async def accept_invite(
        self, invite_id: int, invitee_id: int, *, max_size: int, now: int | None = None
    ) -> tuple[Team, list[Invite]]:
        """Принять приглашение: команда после вступления и погасшие приглашения.

        У автора нет команды — появляется команда из двоих; есть — адресат
        вступает в неё (место проверяет join_team одним запросом).
        """
        async with self._team_lock:
            conn = self._require()
            cursor = await conn.execute(
                f"SELECT {_INVITE_COLUMNS} FROM team_invites WHERE id = ? AND invitee_id = ?",
                (invite_id, invitee_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise InviteGoneError("Приглашения нет")
            invite = _invite_from_row(row)
            try:
                if await self.team_of_student(invitee_id) is not None:
                    raise AlreadyInTeamError("Студент уже в команде")
                team = await self.team_of_student(invite.inviter_id)
                if team is None:
                    team = await self.create_team(
                        [invite.inviter_id, invitee_id], max_size=max_size, now=now
                    )
                else:
                    team = await self.join_team(team.id, invitee_id, max_size=max_size, now=now)
            except (AlreadyInTeamError, TeamFullError, TeamNotFoundError):
                # исполнить нельзя — и ждать больше нечего
                await conn.execute("DELETE FROM team_invites WHERE id = ?", (invite.id,))
                await conn.commit()
                raise
            await conn.execute("DELETE FROM team_invites WHERE id = ?", (invite.id,))
            # Гаснут приглашения тем, кто теперь в команде, и от вступившего:
            # его «присоединяйся ко мне» звало не в эту команду.
            dropped = await self._drop_invites([invitee_id])
            await conn.commit()
        return team, dropped

    async def drop_invites(self, *, inviters: Sequence[int] = ()) -> list[Invite]:
        """Погасить приглашения после смены состава в обход бота — из CLI."""
        async with self._team_lock:
            dropped = await self._drop_invites(inviters)
            await self._require().commit()
        return dropped

    async def _drop_invites(self, inviters: Sequence[int]) -> list[Invite]:
        """Удалить приглашения, которые уже не исполнить, и вернуть их; commit — за вызывающим.

        Гаснут приглашения тем, кто уже в команде, и от тех, кто сменил
        команду (`inviters`).
        """
        conn = self._require()
        condition = "invitee_id IN (SELECT student_id FROM team_members)"
        params = list(inviters)
        if params:
            condition += f" OR inviter_id IN ({', '.join('?' for _ in params)})"
        cursor = await conn.execute(
            f"DELETE FROM team_invites WHERE {condition} RETURNING {_INVITE_COLUMNS}",
            params,
        )
        rows = await cursor.fetchall()
        return sorted((_invite_from_row(row) for row in rows), key=lambda item: item.id)
