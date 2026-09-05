# hwbot

Telegram-бот сбора домашних заданий и живой оценки для курса
«Программирование на Python для бизнес-аналитики» (1 курс БАЦРФ, ВШЭ).

Студент сдаёт ссылку на репозиторий, смотрит свои работы, посещаемость и оценку.
Преподаватель ведёт календарь в `data/course.toml`, отмечает пары и ставит баллы
через CLI.

Как устроен прод и какие команды запускать из нового чата — в [CONTEXT.md](CONTEXT.md).

## Запуск

Python 3.12, [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env
cp data/roster.example.csv data/roster.csv
```

В `.env` нужен `BOT_TOKEN` от [@BotFather](https://t.me/BotFather) и свой
Telegram id в `ADMIN_TELEGRAM_IDS`. Ведомость — ФИО, `group_code`, почта;
колонки с датой рождения бот не принимает.

```bash
uv run hwbot seed-course
uv run hwbot run
```

Тесты без сети: `uv run pytest`. Типы: `uv run mypy src` и `uv run pyright`.

Не коммить `.env`, `data/bot.db` и живую `data/roster.csv`.
