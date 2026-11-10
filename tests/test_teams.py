from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageText,
    GetMe,
    SendMessage,
    TelegramMethod,
)
from aiogram.methods.base import TelegramType
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, User

from hwbot import cli
from hwbot.cli import (
    build_parser,
    run_team_add,
    run_team_create,
    run_team_export,
    run_team_list,
    run_team_remove,
    run_team_rename,
)
from hwbot.commands import ADMIN_COMMANDS, STUDENT_COMMANDS
from hwbot.config import PROJECT_ROOT, Settings
from hwbot.course import DEFAULT_COURSE_PATH, load_course
from hwbot.db import Database
from hwbot.errors import (
    AlreadyInTeamError,
    CourseError,
    InviteGoneError,
    NotInTeamError,
    TeamFullError,
    TeamNotFoundError,
)
from hwbot.formatting import help_text, submit_prompt
from hwbot.handlers import admin, student
from hwbot.handlers.student import InviteAnswer, LeaveTeam, TeamMenu, TeamPick, TeamStates
from hwbot.matching import search_students
from hwbot.models import Assessment, RosterRow, Student
from hwbot.notify import send_team_reminders
from hwbot.ops import seed_course
from hwbot.roster import load_roster
from hwbot.timeutil import parse_deadline

BEFORE_LOCK = parse_deadline("2026-11-15 12:00")
AFTER_LOCK = parse_deadline("2026-11-23 00:00")
ADMIN_ID = 77
IVANOV, PETROVA, SEMENOV, KUZNETSOVA = 101, 102, 103, 104
LOCKED = "Сборка команд закрыта 22 ноября, 23:59."


class RecordingSession(BaseSession):
    """Сессия без сети: запоминает запросы к Telegram, на getMe отвечает именем бота.

    unchanged=True — Telegram отвечает на правку «message is not modified»,
    как бывает при повторном нажатии.
    """

    def __init__(self, *, unchanged: bool = False) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []
        self.unchanged = unchanged

    async def make_request(
        self, bot: Bot, method: TelegramMethod[TelegramType], timeout: int | None = None
    ) -> TelegramType:
        self.requests.append(method)
        if isinstance(method, GetMe):
            me = User(id=42, is_bot=True, first_name="Бот", username="test_bot")
            return cast(TelegramType, me)
        if self.unchanged and isinstance(method, EditMessageText):
            raise TelegramBadRequest(method=method, message="Bad Request: message is not modified")
        return cast(TelegramType, True)

    async def close(self) -> None:
        return None

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        yield b""


def _requests(bot: Bot) -> list[TelegramMethod[Any]]:
    return cast(RecordingSession, bot.session).requests


def sent(bot: Bot) -> list[SendMessage]:
    return [item for item in _requests(bot) if isinstance(item, SendMessage)]


def edits(bot: Bot) -> list[EditMessageText]:
    return [item for item in _requests(bot) if isinstance(item, EditMessageText)]


def alerts(bot: Bot) -> list[str]:
    return [item.text or "" for item in _requests(bot) if isinstance(item, AnswerCallbackQuery)]


def texts_to(bot: Bot, chat_id: int) -> list[str]:
    return [item.text for item in sent(bot) if item.chat_id == chat_id]


def screen(bot: Bot) -> EditMessageText:
    """Последнее перерисованное сообщение — то, что сейчас видит нажавший."""
    return edits(bot)[-1]


def buttons(method: SendMessage | EditMessageText) -> list[tuple[str, str]]:
    markup = method.reply_markup
    if not isinstance(markup, InlineKeyboardMarkup):
        return []
    return [
        (button.text, button.callback_data or "")
        for row in markup.inline_keyboard
        for button in row
    ]


def labels(method: SendMessage | EditMessageText) -> list[str]:
    return [text for text, _data in buttons(method)]


def unpack(data: str) -> CallbackData:
    for kind in (TeamMenu, TeamPick, InviteAnswer, LeaveTeam):
        if data.startswith(f"{kind.__prefix__}:"):
            return kind.unpack(data)
    raise AssertionError(data)


def button(method: SendMessage | EditMessageText, prefix: str) -> CallbackData:
    for text, data in buttons(method):
        if text.startswith(prefix):
            return unpack(data)
    raise AssertionError(f"нет кнопки {prefix!r}: {labels(method)}")


def message(bot: Bot, user_id: int, text: str = "") -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Студент"),
        text=text,
    ).as_(bot)


def callback(bot: Bot, user_id: int, data: CallbackData) -> CallbackQuery:
    return CallbackQuery(
        id="1",
        from_user=User(id=user_id, is_bot=False, first_name="Студент"),
        chat_instance="1",
        data=data.pack(),
        message=message(bot, user_id),
    ).as_(bot)


def fsm(user_id: int) -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=42, chat_id=user_id, user_id=user_id),
    )


def settings() -> Settings:
    return Settings(
        bot_token="test",
        admin_telegram_ids=frozenset({ADMIN_ID}),
        db_path=Path("bot.db"),
        roster_path=Path("roster.csv"),
        timezone="Europe/Moscow",
    )


async def press(
    bot: Bot, db: Database, user_id: int, data: CallbackData, state: FSMContext | None = None
) -> None:
    """Нажать кнопку: вызвать обработчик, как это сделал бы диспетчер."""
    query = callback(bot, user_id, data)
    if isinstance(data, TeamMenu):
        await student.team_menu(query, data, state or fsm(user_id), db)
    elif isinstance(data, TeamPick):
        await student.team_pick(query, data, db)
    elif isinstance(data, InviteAnswer):
        await student.invite_answer(query, data, db)
    elif isinstance(data, LeaveTeam):
        await student.leave_team(query, data, db)
    else:
        raise AssertionError(data)


async def invite(bot: Bot, db: Database, inviter: int, surname: str) -> SendMessage:
    """Позвать из списка «Кто без команды»; вернуть приглашение, пришедшее адресату."""
    await press(bot, db, inviter, TeamMenu(action="list"))
    await press(bot, db, inviter, button(screen(bot), f"Пригласить: {surname}"))
    return sent(bot)[-1]


@pytest.fixture
def bot() -> Bot:
    return Bot(token="42:TEST", session=RecordingSession())


@pytest.fixture
def before_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(student, "now_ts", lambda: BEFORE_LOCK)
    monkeypatch.setattr(admin, "now_ts", lambda: BEFORE_LOCK)
    monkeypatch.setattr(cli, "now_ts", lambda: BEFORE_LOCK)


@pytest.fixture
async def people(db: Database, roster_path: Path) -> dict[int, Student]:
    """Иванов, Петрова (261) и Семёнов (262) зашли в бота; Кузнецова (262) ещё нет."""
    await db.seed_roster(load_roster(roster_path))
    by_surname = {item.full_name.split()[0]: item for item in await db.list_students()}
    found: dict[int, Student] = {}
    for surname, telegram_id in (("Иванов", IVANOV), ("Петрова", PETROVA), ("Семёнов", SEMENOV)):
        found[telegram_id] = await db.bind_telegram(by_surname[surname].id, telegram_id, None)
    found[KUZNETSOVA] = by_surname["Кузнецова"]
    return found


# ──────────────────────────────────────────────────────────────── настройки


def test_course_teams_section() -> None:
    teams = load_course(DEFAULT_COURSE_PATH).teams
    assert (teams.min_size, teams.max_size) == (2, 3)
    assert teams.lock_at_ts == parse_deadline("2026-11-22 23:59")
    assert teams.remind_at_ts == parse_deadline("2026-11-20 12:00")
    assert teams.is_open(teams.lock_at_ts)
    assert not teams.is_open(teams.lock_at_ts + 1)


def test_course_rejects_broken_teams(tmp_path: Path) -> None:
    text = (PROJECT_ROOT / "data" / "course.toml").read_text(encoding="utf-8")
    path = tmp_path / "broken.toml"
    for broken, error in (
        (text.replace("min_size = 2", "min_size = 4"), "min_size"),
        (text.replace("min_size = 2", "min_size = 1"), "min_size"),
        (text.replace("remind_at = 2026-11-20T12:00", "remind_at = 2026-11-23T12:00"), "remind_at"),
        (text.replace("[teams]", "[teams_typo]"), r"\[teams\]"),
    ):
        path.write_text(broken, encoding="utf-8")
        with pytest.raises(CourseError, match=error):
            load_course(path)


def test_pre_defense_lessons_are_named_so() -> None:
    titles = {item.code: item.title for item in load_course(DEFAULT_COURSE_PATH).lessons}
    assert titles["S12A"] == titles["S12B"] == "Предзащита итоговых проектов"


# ─────────────────────────────────────────────────────────────── база


async def test_one_team_per_student_is_held_by_the_database(
    db: Database, people: dict[int, Student], tmp_path: Path
) -> None:
    ivanov, semenov = people[IVANOV], people[SEMENOV]
    first = await db.create_team([ivanov.id], max_size=3)
    with pytest.raises(AlreadyInTeamError):
        await db.create_team([semenov.id, ivanov.id], max_size=3)
    # неудачная попытка не оставила ни пустой команды, ни Семёнова в ней
    assert [team.id for team in await db.list_teams()] == [first.id]
    assert await db.team_of_student(semenov.id) is None
    # и в обход кода, прямо SQL-ем, вторую команду не записать
    second = await db.create_team([semenov.id], max_size=3)
    raw = sqlite3.connect(tmp_path / "test.db")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(
                "INSERT INTO team_members (student_id, team_id, joined_at) VALUES (?, ?, 0)",
                (ivanov.id, second.id),
            )
    finally:
        raw.close()


async def test_join_full_and_missing_team(db: Database, people: dict[int, Student]) -> None:
    team = await db.create_team([people[IVANOV].id, people[PETROVA].id], max_size=3)
    joined = await db.join_team(team.id, people[SEMENOV].id, max_size=3)
    assert [item.full_name.split()[0] for item in joined.members] == ["Иванов", "Петрова", "Семёнов"]
    assert joined.name is None
    with pytest.raises(TeamFullError):
        await db.join_team(team.id, people[KUZNETSOVA].id, max_size=3)
    with pytest.raises(TeamNotFoundError):
        await db.join_team(999, people[KUZNETSOVA].id, max_size=3)


async def test_invites_cap_duplicates_and_roles(db: Database, people: dict[int, Student]) -> None:
    ivanov, petrova, semenov, kuznetsova = (people[key].id for key in (IVANOV, PETROVA, SEMENOV, KUZNETSOVA))
    first = await db.send_invite(ivanov, petrova, max_size=3)
    assert first is not None
    assert await db.send_invite(ivanov, petrova, max_size=3) is None  # уже ждёт ответа
    assert await db.send_invite(ivanov, semenov, max_size=3) is not None
    # одиночка и двое ждущих — три места заняты
    with pytest.raises(TeamFullError):
        await db.send_invite(ivanov, kuznetsova, max_size=3)
    with pytest.raises(ValueError):
        await db.send_invite(ivanov, ivanov, max_size=3)

    # отказать может только адресат, отозвать — только автор
    assert await db.take_invite(first.id, ivanov, as_inviter=False) is None
    assert await db.take_invite(first.id, petrova, as_inviter=True) is None
    assert len(await db.list_invites()) == 2
    assert await db.take_invite(first.id, petrova, as_inviter=False) == first
    assert await db.take_invite(first.id, petrova, as_inviter=False) is None

    # пара зовёт на одно оставшееся место, и приглашение напарника тоже его занимает
    await db.create_team([petrova, kuznetsova], max_size=3)
    with pytest.raises(AlreadyInTeamError):
        await db.send_invite(ivanov, petrova, max_size=3)
    assert await db.send_invite(petrova, ivanov, max_size=3) is not None
    with pytest.raises(TeamFullError):
        await db.send_invite(kuznetsova, semenov, max_size=3)


async def test_accept_creates_team_and_drops_stale_invites(
    db: Database, people: dict[int, Student]
) -> None:
    ivanov, petrova, semenov, kuznetsova = (people[key].id for key in (IVANOV, PETROVA, SEMENOV, KUZNETSOVA))
    from_petrova = await db.send_invite(petrova, semenov, max_size=3)
    from_ivanov = await db.send_invite(ivanov, semenov, max_size=3)
    from_semenov = await db.send_invite(semenov, kuznetsova, max_size=3)
    assert from_petrova and from_ivanov and from_semenov

    team, dropped = await db.accept_invite(from_ivanov.id, semenov, max_size=3)
    assert [member.id for member in team.members] == [ivanov, semenov]
    # гаснут: приглашение Семёнову от Петровой и «присоединяйся ко мне» от Семёнова
    assert dropped == [from_petrova, from_semenov]
    assert await db.list_invites() == []
    with pytest.raises(InviteGoneError):
        await db.accept_invite(from_ivanov.id, semenov, max_size=3)


async def test_two_accepts_for_the_last_place(db: Database, people: dict[int, Student], tmp_path: Path) -> None:
    ivanov, petrova, semenov, kuznetsova = (people[key].id for key in (IVANOV, PETROVA, SEMENOV, KUZNETSOVA))
    team = await db.create_team([ivanov, petrova], max_size=3)
    # два приглашения на одно место: так бывает, если преподаватель менял состав в CLI
    raw = sqlite3.connect(tmp_path / "test.db")
    raw.executemany(
        "INSERT INTO team_invites (inviter_id, invitee_id, created_at) VALUES (?, ?, 0)",
        [(ivanov, semenov), (petrova, kuznetsova)],
    )
    raw.commit()
    raw.close()
    first, second = await db.list_invites()
    results = await asyncio.gather(
        db.accept_invite(first.id, semenov, max_size=3),
        db.accept_invite(second.id, kuznetsova, max_size=3),
        return_exceptions=True,
    )
    assert sum(isinstance(item, TeamFullError) for item in results) == 1
    full = await db.get_team(team.id)
    assert full is not None and len(full.members) == 3
    assert await db.list_invites() == []


async def test_leave_dissolves_a_pair_only_through_the_bot(
    db: Database, people: dict[int, Student]
) -> None:
    ivanov, petrova, semenov = (people[key].id for key in (IVANOV, PETROVA, SEMENOV))
    team = await db.create_team([ivanov, petrova], max_size=3)
    outgoing = await db.send_invite(ivanov, semenov, max_size=3)
    before, dropped = await db.leave_team(ivanov, dissolve=True)
    assert [member.id for member in before.members] == [ivanov, petrova]
    assert dropped == [outgoing]  # ушедший больше не зовёт в прежнюю команду
    assert await db.get_team(team.id) is None
    assert await db.team_of_student(petrova) is None

    # из CLI пара не распадается: преподаватель убирает одного и добавляет другого
    team = await db.create_team([ivanov, petrova], max_size=3)
    await db.leave_team(petrova)
    left = await db.get_team(team.id)
    assert left is not None and [member.id for member in left.members] == [ivanov]
    await db.leave_team(ivanov)
    assert await db.list_teams() == []
    with pytest.raises(NotInTeamError):
        await db.leave_team(ivanov)


# ────────────────────────────────────────────────────────────── поиск


def test_search_by_word_starts_and_typos(roster_students: list[Student]) -> None:
    def find(query: str) -> list[str]:
        return [item.full_name.split()[0] for item in search_students(query, roster_students)]

    assert find("петр") == ["Петрова"]
    assert find("СЕМЕН") == ["Семёнов"]  # «ё» и регистр не важны
    assert find("сем фед") == ["Семёнов"]
    assert find("ан") == ["Петрова"]  # начало имени тоже ищется
    assert find("иваноф") == ["Иванов"]  # опечатка
    assert find("кузнецва") == ["Кузнецова"]
    assert find("zzz") == []
    assert find("   ") == []


# ───────────────────────────────────────────────────── студент: /team


async def test_card_without_team(bot: Bot, db: Database, people: dict[int, Student], before_lock: None) -> None:
    await student.cmd_team(message(bot, IVANOV, "/team"), fsm(IVANOV), db)
    card = sent(bot)[-1]
    assert "2–3 человека" in card.text
    assert "до 22 ноября, 23:59" in card.text
    assert "распределит преподаватель" in card.text
    assert labels(card) == ["Найти по фамилии", "Кто без команды"]


async def test_invite_and_accept(bot: Bot, db: Database, people: dict[int, Student], before_lock: None) -> None:
    await press(bot, db, IVANOV, TeamMenu(action="list"))
    page = screen(bot)
    assert (page.text or "").startswith("Без команды — 3 человека:")
    assert "Кузнецова Мария Алексеевна, 262 · ещё не в боте" in (page.text or "")
    assert labels(page) == ["Пригласить: Петрова А.С.", "Пригласить: Семёнов Ф.Н.", "К команде"]

    await press(bot, db, IVANOV, button(page, "Пригласить: Петрова"))
    assert alerts(bot)[-1] == "Петрова А.С.: приглашение отправлено, ответ придёт сюда."
    offer = sent(bot)[-1]
    assert offer.chat_id == PETROVA
    assert offer.text.startswith("📌 Иванов Иван Иванович (261) зовёт тебя в команду")
    assert "— Иванов Иван Иванович, 261" in offer.text
    assert "разных групп" not in offer.text
    assert labels(offer) == ["Принять", "Отказаться"]

    card = screen(bot)  # у Иванова список превратился в карточку
    assert "⏳ Ждут ответа на твоё приглашение:\n— Петрова Анна Сергеевна, 261" in (card.text or "")
    assert "Отозвать: Петрова А.С." in labels(card)

    accept = button(offer, "Принять")
    await press(bot, db, PETROVA, accept)
    assert alerts(bot)[-1] == "Готово: ты в команде, вас 2."
    mine = screen(bot)
    assert (mine.text or "").startswith("📌 <b>Твоя команда</b> · без названия")
    assert labels(mine) == ["Найти по фамилии", "Кто без команды", "Название", "Выйти из команды"]
    assert texts_to(bot, IVANOV)[-1] == (
        "✅ Петрова Анна Сергеевна, 261 — теперь в твоей команде. Вас 2, состав — в /team."
    )

    # повторное нажатие ничего не ломает
    await press(bot, db, PETROVA, accept)
    assert alerts(bot)[-1] == "Это приглашение уже не действует."
    (team,) = await db.list_teams()
    assert len(team.members) == 2


async def test_mixed_groups_decline_and_strangers(
    bot: Bot, db: Database, people: dict[int, Student], before_lock: None
) -> None:
    offer = await invite(bot, db, IVANOV, "Семёнов")
    assert "Вы из разных групп: на предзащиту команда приходит вместе" in offer.text

    # чужой нажал на «Принять» — ничего не происходит
    await press(bot, db, PETROVA, button(offer, "Принять"))
    assert alerts(bot)[-1] == "Это приглашение уже не действует."
    assert await db.list_teams() == []

    await press(bot, db, SEMENOV, button(offer, "Отказаться"))
    assert alerts(bot)[-1] == "Приглашение отклонено."
    assert texts_to(bot, IVANOV)[-1] == "Семёнов Ф.Н.: приглашение отклонено. Позвать кого-нибудь ещё — /team."
    assert await db.list_invites() == []


async def test_only_inviter_can_withdraw(
    bot: Bot, db: Database, people: dict[int, Student], before_lock: None
) -> None:
    offer = await invite(bot, db, IVANOV, "Петрова")
    card = screen(bot)
    (pending,) = await db.list_invites()
    withdraw = InviteAnswer(action="cancel", invite_id=pending.id)
    await press(bot, db, PETROVA, withdraw)  # кнопку подделали
    assert alerts(bot)[-1] == "Это приглашение уже не действует."
    assert await db.list_invites() == [pending]

    await press(bot, db, IVANOV, button(card, "Отозвать: Петрова"))
    assert alerts(bot)[-1] == "Приглашение отозвано."
    assert texts_to(bot, PETROVA)[-1] == "Иванов И.И.: приглашение отозвано."
    await press(bot, db, PETROVA, button(offer, "Принять"))
    assert alerts(bot)[-1] == "Это приглашение уже не действует."


async def test_mutual_invites_and_stale_ones(
    bot: Bot, db: Database, people: dict[int, Student], before_lock: None
) -> None:
    await invite(bot, db, PETROVA, "Иванов")
    await invite(bot, db, SEMENOV, "Иванов")
    # у Иванова Петрова и Семёнов «зовут тебя» — вместо «Пригласить» стоит «Принять»
    await press(bot, db, IVANOV, TeamMenu(action="list"))
    page = screen(bot)
    assert "Петрова Анна Сергеевна, 261 · зовёт тебя" in (page.text or "")
    assert "Пригласить: Петрова А.С." not in labels(page)
    # встречное приглашение подделанной кнопкой не уходит
    await press(bot, db, IVANOV, TeamPick(student_id=people[PETROVA].id))
    assert alerts(bot)[-1] == "Петрова А.С.: уже зовёт тебя. Прими приглашение в карточке команды."

    await press(bot, db, IVANOV, button(page, "Принять: Петрова"))
    team = await db.team_of_student(people[IVANOV].id)
    assert team is not None and len(team.members) == 2
    assert texts_to(bot, SEMENOV)[-1] == (
        "Иванов И.И. уже в другой команде — твоё приглашение больше не действует."
    )
    assert await db.list_invites() == []


async def test_no_room_means_no_invite_buttons(
    bot: Bot, db: Database, people: dict[int, Student], before_lock: None
) -> None:
    await db.bind_telegram(people[KUZNETSOVA].id, KUZNETSOVA, None)
    await invite(bot, db, IVANOV, "Петрова")
    await invite(bot, db, IVANOV, "Семёнов")
    card = screen(bot)
    assert "Найти по фамилии" not in labels(card)
    await press(bot, db, IVANOV, TeamMenu(action="list"))
    page = screen(bot)
    assert [text for text in labels(page) if text.startswith("Пригласить")] == []
    assert "Свободных мест нет" in (page.text or "")
    await press(bot, db, IVANOV, TeamPick(student_id=people[KUZNETSOVA].id))
    assert alerts(bot)[-1].startswith("Свободных мест нет")


async def test_leave_three_then_pair_dissolves(
    bot: Bot, db: Database, people: dict[int, Student], before_lock: None
) -> None:
    await db.create_team([people[key].id for key in (IVANOV, PETROVA, SEMENOV)], max_size=3)
    await press(bot, db, SEMENOV, TeamMenu(action="leave"))
    confirm = screen(bot)
    assert (confirm.text or "").startswith("Точно выйти из команды?\n\nОстальным я сообщу.")
    assert labels(confirm) == ["Да, выйти", "Нет, остаюсь"]
    await press(bot, db, SEMENOV, LeaveTeam(action="no"))
    assert await db.team_of_student(people[SEMENOV].id) is not None

    await press(bot, db, SEMENOV, LeaveTeam(action="yes"))
    assert alerts(bot)[-1] == "Готово: ты больше не в команде."
    notice = "Семёнов Ф.Н. больше не в твоей команде — вас 2. Позвать кого-нибудь на свободное место — /team."
    assert texts_to(bot, IVANOV)[-1] == texts_to(bot, PETROVA)[-1] == notice

    await press(bot, db, PETROVA, TeamMenu(action="leave"))
    assert "Вас двое — команда распадётся" in (screen(bot).text or "")
    await press(bot, db, PETROVA, LeaveTeam(action="yes"))
    assert texts_to(bot, IVANOV)[-1].startswith(
        "Петрова А.С. больше не в твоей команде. Команды из одного человека не бывает"
    )
    assert await db.list_teams() == []


async def test_search_by_surname(bot: Bot, db: Database, people: dict[int, Student], before_lock: None) -> None:
    state = fsm(IVANOV)
    await press(bot, db, IVANOV, TeamMenu(action="find"), state)
    assert await state.get_state() == TeamStates.search.state
    assert (screen(bot).text or "").startswith("Напиши фамилию однокурсника")

    await student.team_search(message(bot, IVANOV, "с"), state, db)
    assert sent(bot)[-1].text == "Нужно хотя бы две буквы. Напиши фамилию ещё раз."
    assert await state.get_state() == TeamStates.search.state

    await student.team_search(message(bot, IVANOV, "семен"), state, db)
    found = sent(bot)[-1]
    assert found.text.startswith("По запросу «семен» — 1 человек:")
    assert "— Семёнов Фёдор Николаевич, 262" in found.text
    assert labels(found) == ["Пригласить: Семёнов Ф.Н.", "Искать ещё", "К команде"]
    assert await state.get_state() is None  # поиск одноразовый

    await press(bot, db, IVANOV, TeamMenu(action="find"), state)
    await student.team_search(message(bot, IVANOV, "иван"), state, db)  # себя не показывает
    assert sent(bot)[-1].text.startswith("По запросу «иван» никого не нашёл")


async def test_submission_link_during_search_goes_to_submit(
    bot: Bot, db: Database, people: dict[int, Student], before_lock: None
) -> None:
    await db.create_team([people[IVANOV].id, people[PETROVA].id], max_size=3)
    for action, handler in (("find", student.team_search), ("name", student.team_rename)):
        state = fsm(IVANOV)
        await press(bot, db, IVANOV, TeamMenu(action=action), state)
        with pytest.raises(SkipHandler):
            await handler(message(bot, IVANOV, "https://github.com/me/prime-monitor/pull/3"), state, db)
        assert await state.get_state() is None


async def test_team_name(bot: Bot, db: Database, people: dict[int, Student], before_lock: None) -> None:
    await db.create_team([people[IVANOV].id, people[PETROVA].id], max_size=3)
    state = fsm(IVANOV)
    await press(bot, db, IVANOV, TeamMenu(action="name"), state)
    assert (screen(bot).text or "").endswith("Сейчас названия нет.")

    await student.team_rename(message(bot, IVANOV, "x" * 41), state, db)
    assert sent(bot)[-1].text.startswith("Название — от 2 до 40 символов.")
    assert await state.get_state() == TeamStates.name.state

    await student.team_rename(message(bot, IVANOV, "  Data   <Rangers>  "), state, db)
    assert sent(bot)[-1].text.startswith("📌 <b>Команда «Data &lt;Rangers&gt;»</b>")
    assert texts_to(bot, PETROVA)[-1] == "Новое название команды: «Data &lt;Rangers&gt;»."
    (team,) = await db.list_teams()
    assert team.name == "Data <Rangers>"
    assert await state.get_state() is None


async def test_list_pages(bot: Bot, db: Database, people: dict[int, Student], before_lock: None) -> None:
    await db.seed_roster(
        [RosterRow(f"Студентов{n} Тест Тестович", "БАЦРФ261", f"s{n}@example.edu") for n in range(10)]
    )
    await press(bot, db, IVANOV, TeamMenu(action="list"))
    first = screen(bot)
    assert (first.text or "").startswith("Без команды — 13 человек, страница 1 из 2:")
    assert labels(first)[-2:] == ["К команде", "→"]
    await press(bot, db, IVANOV, TeamMenu(action="list", page=7))  # за краем — последняя
    last = screen(bot)
    assert (last.text or "").startswith("Без команды — 13 человек, страница 2 из 2:")
    assert labels(last)[-2:] == ["←", "К команде"]


async def test_same_screen_twice_is_not_an_error(
    db: Database, people: dict[int, Student], before_lock: None
) -> None:
    bot = Bot(token="42:TEST", session=RecordingSession(unchanged=True))
    await press(bot, db, IVANOV, TeamMenu(action="list"))
    await press(bot, db, IVANOV, TeamMenu(action="list"))
    assert len(edits(bot)) == 2


async def test_everything_is_read_only_after_lock(
    bot: Bot, db: Database, people: dict[int, Student], monkeypatch: pytest.MonkeyPatch
) -> None:
    await db.create_team([people[IVANOV].id, people[PETROVA].id], max_size=3)
    pending = await db.send_invite(people[IVANOV].id, people[SEMENOV].id, max_size=3)
    assert pending is not None
    monkeypatch.setattr(student, "now_ts", lambda: AFTER_LOCK)

    await student.cmd_team(message(bot, IVANOV), fsm(IVANOV), db)
    card = sent(bot)[-1]
    assert f"{LOCKED} Менять состав теперь может только преподаватель" in card.text
    assert "Ждут ответа" not in card.text
    assert buttons(card) == []

    await student.cmd_team(message(bot, SEMENOV), fsm(SEMENOV), db)
    assert "Сборка закрылась 22 ноября, 23:59" in sent(bot)[-1].text
    assert buttons(sent(bot)[-1]) == []

    for user, data in (
        (SEMENOV, TeamMenu(action="list")),
        (SEMENOV, TeamPick(student_id=people[IVANOV].id)),
        (SEMENOV, InviteAnswer(action="yes", invite_id=pending.id)),
        (PETROVA, LeaveTeam(action="yes")),
    ):
        await press(bot, db, user, data)
        assert alerts(bot)[-1].startswith(LOCKED)
    assert await db.team_of_student(people[SEMENOV].id) is None
    assert await db.team_of_student(people[PETROVA].id) is not None


async def test_team_reminder_goes_once_to_those_without_team(
    bot: Bot, db: Database, people: dict[int, Student]
) -> None:
    course = load_course(DEFAULT_COURSE_PATH)
    await seed_course(db, course)
    await db.create_team([people[IVANOV].id, people[PETROVA].id], max_size=3)
    remind_at = course.teams.remind_at_ts
    assert remind_at is not None
    assert await send_team_reminders(bot, db, now=remind_at - 60, course=course) == 0
    # Семёнов без команды; Кузнецова без команды, но не зашла в бота
    assert await send_team_reminders(bot, db, now=remind_at, course=course) == 1
    (text,) = texts_to(bot, SEMENOV)
    assert text.startswith("⏳ До 22 ноября, 23:59 нужно собраться в команду")
    assert await send_team_reminders(bot, db, now=remind_at + 300, course=course) == 0
    assert await send_team_reminders(bot, db, now=course.teams.lock_at_ts + 1, course=course) == 0


# ─────────────────────────────────────────────────────────── преподаватель


async def test_admin_teams_board(bot: Bot, db: Database, people: dict[int, Student], before_lock: None) -> None:
    await admin.cmd_teams(message(bot, IVANOV), db, settings())
    assert sent(bot)[-1].text == "Это команда преподавателя."

    team = await db.create_team([people[IVANOV].id, people[PETROVA].id], max_size=3)
    await db.rename_team(team.id, "Альфа")
    await db.send_invite(people[IVANOV].id, people[SEMENOV].id, max_size=3)
    await admin.cmd_teams(message(bot, ADMIN_ID), db, settings())
    board = sent(bot)[-1].text
    assert board.splitlines()[:3] == [
        "Команды итогового проекта · сборка до 22 ноября, 23:59",
        "Команд 1 · распределено 2 из 4 · без команды 2",
        "Приглашений без ответа: 1",
    ]
    assert f"Команда {team.id} · Альфа\n— Иванов И.И. (261)\n— Петрова А.С. (261)" in board
    assert board.endswith("Без команды · 2\n— Кузнецова М.А. (262)\n— Семёнов Ф.Н. (262)")


async def test_cli_team_commands(
    db: Database, people: dict[int, Student], before_lock: None, capsys: pytest.CaptureFixture[str]
) -> None:
    course = load_course(DEFAULT_COURSE_PATH)
    assert await run_team_create(db, course, "Иванов Иван Иванович, petrova@example.edu", dry_run=True) == 0
    assert "dry-run, новая команда: Иванов Иван Иванович, Петрова Анна Сергеевна" in capsys.readouterr().out
    assert await db.list_teams() == []

    assert await run_team_create(db, course, "Иванов Иван Иванович, petrova@example.edu", dry_run=False) == 0
    (team,) = await db.list_teams()
    assert f"Создал команду {team.id}: Иванов Иван Иванович, Петрова Анна Сергеевна" in capsys.readouterr().out

    # приглашение Семёнову от Кузнецовой гаснет, когда преподаватель добавляет его в команду
    await db.send_invite(people[KUZNETSOVA].id, people[SEMENOV].id, max_size=3)
    assert await run_team_add(db, course, "ABC234", "semenov@example.edu", dry_run=False) == 1
    assert "Нет команды ABC234" in capsys.readouterr().err
    assert await run_team_add(db, course, str(team.id), "semenov@example.edu", dry_run=True) == 0
    assert "dry-run, Семёнов Фёдор Николаевич → команда" in capsys.readouterr().out
    assert await db.team_of_student(people[SEMENOV].id) is None
    assert await run_team_add(db, course, str(team.id), "semenov@example.edu", dry_run=False) == 0
    assert "в ней 3 из 3" in capsys.readouterr().out
    assert await db.list_invites() == []
    assert await run_team_add(db, course, str(team.id), "kuznetsova@example.edu", dry_run=False) == 1
    assert "заполнена: 3 из 3" in capsys.readouterr().err

    assert await run_team_rename(db, str(team.id), "  Альфа  ") == 0
    assert f"Команда {team.id}: название «Альфа»" in capsys.readouterr().out
    assert await run_team_rename(db, str(team.id), "") == 0
    assert f"Команда {team.id}: без названия" in capsys.readouterr().out

    assert await run_team_remove(db, "semenov@example.edu", dry_run=False) == 0
    assert await run_team_remove(db, "petrova@example.edu", dry_run=False) == 0
    assert "← из команды" in capsys.readouterr().out
    solo = await db.get_team(team.id)  # из CLI пара не распадается
    assert solo is not None and len(solo.members) == 1

    assert await run_team_list(db, course) == 0
    assert "Команд 1 · распределено 1 из 4 · без команды 3" in capsys.readouterr().out
    assert await run_team_export(db, None) == 0
    rows = capsys.readouterr().out.splitlines()
    assert rows[0] == "team,name,full_name,group_code,email"
    assert f"{team.id},,Иванов Иван Иванович,БАЦРФ261,ivanov@example.edu" in rows
    assert ",,Семёнов Фёдор Николаевич,БАЦРФ262,semenov@example.edu" in rows

    args = build_parser().parse_args(["team", "rename", "--team", "3", "--name", "Бета"])
    assert (args.team_command, args.team, args.name) == ("rename", "3", "Бета")


# ─────────────────────────────────────────────────────── меню и подсказки


def _assessment(code: str, label: str) -> Assessment:
    return Assessment(
        id=1,
        code=code,
        label=label,
        title=label,
        body="",
        component=code,
        weight_final=0.1,
        submit_via_bot=True,
        issued_at=1,
        deadline_ts=2,
        accept_until_ts=3,
        graded_on_ts=None,
        late_rule="none",
        blocking=False,
        active=True,
    )


def test_exam_submit_prompt_asks_for_team_repository() -> None:
    exam = submit_prompt(_assessment("exam", "Экзамен"))
    assert "ссылку на репозиторий команды" in exam
    assert "каждый участник команды" in exam
    assert "pull request" not in exam
    assert "pull request" in submit_prompt(_assessment("hw3", "ДЗ-3"))


def test_team_in_menu_and_help() -> None:
    assert "team" in [item.command for item in STUDENT_COMMANDS]
    assert "teams" in [item.command for item in ADMIN_COMMANDS]
    assert "/team — команда для итогового проекта" in help_text()
