from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import aiosqlite

from hwbot.errors import (
    AlreadyBoundError,
    DeadlineClosedError,
    HomeworkNotFoundError,
    StudentTakenError,
)
from hwbot.models import Homework, HomeworkStatusRow, RosterRow, Student, Submission
from hwbot.timeutil import is_deadline_open, now_ts

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL UNIQUE,
    group_code TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    telegram_id INTEGER UNIQUE,
    telegram_username TEXT
);

CREATE TABLE IF NOT EXISTS homeworks (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    deadline_ts INTEGER NOT NULL,
    groups_json TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL,
    homework_id INTEGER NOT NULL,
    payload TEXT NOT NULL,
    submitted_at INTEGER NOT NULL,
    FOREIGN KEY (student_id) REFERENCES students(id),
    FOREIGN KEY (homework_id) REFERENCES homeworks(id)
);

CREATE TABLE IF NOT EXISTS reminders_sent (
    homework_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    window TEXT NOT NULL,
    sent_at INTEGER NOT NULL,
    PRIMARY KEY (homework_id, student_id, window),
    FOREIGN KEY (homework_id) REFERENCES homeworks(id),
    FOREIGN KEY (student_id) REFERENCES students(id)
);
"""


def _student_from_row(row: aiosqlite.Row) -> Student:
    return Student(
        id=int(row["id"]),
        full_name=str(row["full_name"]),
        group_code=str(row["group_code"]),
        email=str(row["email"]),
        telegram_id=None if row["telegram_id"] is None else int(row["telegram_id"]),
        telegram_username=(
            None if row["telegram_username"] is None else str(row["telegram_username"])
        ),
    )


def _homework_from_row(row: aiosqlite.Row) -> Homework:
    groups = tuple(cast(list[str], json.loads(str(row["groups_json"]))))
    return Homework(
        id=int(row["id"]),
        title=str(row["title"]),
        body=str(row["body"]),
        deadline_ts=int(row["deadline_ts"]),
        group_codes=groups,
        active=bool(row["active"]),
        created_at=int(row["created_at"]),
    )


def _submission_from_row(row: aiosqlite.Row) -> Submission:
    return Submission(
        id=int(row["id"]),
        student_id=int(row["student_id"]),
        homework_id=int(row["homework_id"]),
        payload=str(row["payload"]),
        submitted_at=int(row["submitted_at"]),
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
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def _require(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected")
        return self._conn

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
                INSERT INTO students (full_name, group_code, email)
                VALUES (?, ?, ?)
                ON CONFLICT(full_name) DO UPDATE SET
                    group_code = excluded.group_code,
                    email = excluded.email
                """,
                (row.full_name, row.group_code, row.email),
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

    async def create_homework(
        self,
        title: str,
        body: str,
        deadline_ts: int,
        group_codes: Sequence[str],
        created_at: int | None = None,
    ) -> Homework:
        conn = self._require()
        created = now_ts() if created_at is None else created_at
        cursor = await conn.execute(
            """
            INSERT INTO homeworks (title, body, deadline_ts, groups_json, active, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (title, body, deadline_ts, json.dumps(list(group_codes), ensure_ascii=False), created),
        )
        await conn.commit()
        homework_id = cursor.lastrowid
        assert homework_id is not None
        homework = await self.get_homework(int(homework_id))
        assert homework is not None
        return homework

    async def get_homework(self, homework_id: int) -> Homework | None:
        conn = self._require()
        cursor = await conn.execute("SELECT * FROM homeworks WHERE id = ?", (homework_id,))
        row = await cursor.fetchone()
        return None if row is None else _homework_from_row(row)

    async def list_homeworks(self, active_only: bool = False) -> list[Homework]:
        conn = self._require()
        if active_only:
            cursor = await conn.execute(
                "SELECT * FROM homeworks WHERE active = 1 ORDER BY deadline_ts"
            )
        else:
            cursor = await conn.execute("SELECT * FROM homeworks ORDER BY deadline_ts")
        return [_homework_from_row(row) for row in await cursor.fetchall()]

    async def homeworks_for_group(self, group_code: str) -> list[Homework]:
        result: list[Homework] = []
        for homework in await self.list_homeworks(active_only=True):
            if group_code in homework.group_codes:
                result.append(homework)
        return result

    async def latest_submission(
        self, student_id: int, homework_id: int
    ) -> Submission | None:
        conn = self._require()
        cursor = await conn.execute(
            """
            SELECT * FROM submissions
            WHERE student_id = ? AND homework_id = ?
            ORDER BY submitted_at DESC, id DESC
            LIMIT 1
            """,
            (student_id, homework_id),
        )
        row = await cursor.fetchone()
        return None if row is None else _submission_from_row(row)

    async def list_student_submissions(self, student_id: int) -> list[tuple[Homework, Submission]]:
        conn = self._require()
        cursor = await conn.execute(
            """
            SELECT s.*, h.title, h.body, h.deadline_ts, h.groups_json, h.active, h.created_at
            FROM submissions s
            JOIN homeworks h ON h.id = s.homework_id
            WHERE s.student_id = ?
            ORDER BY s.submitted_at DESC, s.id DESC
            """,
            (student_id,),
        )
        latest: dict[int, tuple[Homework, Submission]] = {}
        for row in await cursor.fetchall():
            submission = _submission_from_row(row)
            if submission.homework_id in latest:
                continue
            homework = Homework(
                id=submission.homework_id,
                title=str(row["title"]),
                body=str(row["body"]),
                deadline_ts=int(row["deadline_ts"]),
                group_codes=tuple(cast(list[str], json.loads(str(row["groups_json"])))),
                active=bool(row["active"]),
                created_at=int(row["created_at"]),
            )
            latest[submission.homework_id] = (homework, submission)
        return list(latest.values())

    async def add_submission(
        self,
        student_id: int,
        homework_id: int,
        payload: str,
        submitted_at: int | None = None,
    ) -> Submission:
        homework = await self.get_homework(homework_id)
        if homework is None or not homework.active:
            raise HomeworkNotFoundError("Задание не найдено")
        moment = now_ts() if submitted_at is None else submitted_at
        if not is_deadline_open(homework.deadline_ts, moment):
            raise DeadlineClosedError("Дедлайн уже прошёл")
        student = await self.get_student(student_id)
        if student is None:
            raise HomeworkNotFoundError("Студент не найден")
        if student.group_code not in homework.group_codes:
            raise HomeworkNotFoundError("Это задание не для твоей группы")
        text = payload.strip()
        if not text:
            raise ValueError("Пустая сдача")
        conn = self._require()
        cursor = await conn.execute(
            """
            INSERT INTO submissions (student_id, homework_id, payload, submitted_at)
            VALUES (?, ?, ?, ?)
            """,
            (student_id, homework_id, text, moment),
        )
        await conn.commit()
        submission_id = cursor.lastrowid
        assert submission_id is not None
        saved = await self.latest_submission(student_id, homework_id)
        assert saved is not None
        return saved

    async def homework_status(self, homework_id: int) -> list[HomeworkStatusRow]:
        homework = await self.get_homework(homework_id)
        if homework is None:
            raise HomeworkNotFoundError("Задание не найдено")
        students = await self.students_in_groups(homework.group_codes)
        rows: list[HomeworkStatusRow] = []
        for student in students:
            submission = await self.latest_submission(student.id, homework_id)
            rows.append(HomeworkStatusRow(student=student, submission=submission))
        return rows

    async def reminder_was_sent(
        self, homework_id: int, student_id: int, window: str
    ) -> bool:
        conn = self._require()
        cursor = await conn.execute(
            """
            SELECT 1 FROM reminders_sent
            WHERE homework_id = ? AND student_id = ? AND window = ?
            """,
            (homework_id, student_id, window),
        )
        return await cursor.fetchone() is not None

    async def mark_reminder_sent(
        self, homework_id: int, student_id: int, window: str, sent_at: int | None = None
    ) -> None:
        conn = self._require()
        await conn.execute(
            """
            INSERT OR IGNORE INTO reminders_sent (homework_id, student_id, window, sent_at)
            VALUES (?, ?, ?, ?)
            """,
            (homework_id, student_id, window, now_ts() if sent_at is None else sent_at),
        )
        await conn.commit()

    async def sent_reminder_keys(self) -> set[tuple[int, int, str]]:
        conn = self._require()
        cursor = await conn.execute(
            "SELECT homework_id, student_id, window FROM reminders_sent"
        )
        return {
            (int(row["homework_id"]), int(row["student_id"]), str(row["window"]))
            for row in await cursor.fetchall()
        }
