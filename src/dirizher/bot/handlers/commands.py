"""Команды бота: справка, режимы, доска, профиль, заметки и база знаний."""

from __future__ import annotations

from html import escape as esc

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ...container import AppContainer
from ...domain.models import TeamMember
from .. import keyboards as kb
from .. import text as tx

router = Router(name="commands")

HELP = """\
🎼 <b>Дирижёр</b> — AI-помощник project-менеджера.

Я нахожу задачи в переписке и встречах, веду YouGile, напоминаю о сроках,
собираю отчёты и показываю личную картину по задачам.

<b>Работа с задачами</b>
/mode auto|manual — авто-режим или подтверждение
/board — канбан-доска
/tasks — мои открытые задачи
/report текст — вечерний отчёт
/reconcile — сверка отчётов сейчас
/remind — проверить дедлайны

<b>Личный кабинет</b>
/profile — профиль, метрики, XP и ачивки
/note текст — добавить личную заметку
/notes — показать мои заметки
/whoami — как я вас вижу
/join — связать Telegram с профилем
/register Имя; алиасы — имя и прозвища

<b>База знаний</b>
/kb — последние знания команды
/kb add Заголовок | текст — добавить запись
/kb find запрос — найти запись
"""


def _is_private(message: Message) -> bool:
    return getattr(message.chat, "type", "") == "private"


def _member_from_message(message: Message, c: AppContainer) -> TeamMember:
    user = message.from_user
    member = TeamMember(
        user_id=user.id if user else None,
        username=user.username if user else None,
        full_name=user.full_name if user else "участник",
        dm_chat_id=message.chat.id if _is_private(message) else None,
    )
    saved = c.team.register(member)
    if user and _is_private(message):
        c.team.attach_dm_chat(user.id, message.chat.id)
    return saved


def _report_chat_id(message: Message, c: AppContainer, member: TeamMember) -> int:
    """Чат, к которому относится отчёт: группа, team_chat_id или источник задач."""
    if not _is_private(message):
        return message.chat.id
    if c.settings.telegram.team_chat_id:
        return c.settings.telegram.team_chat_id
    name = member.username or member.full_name
    for task in c.repo.open_by_assignee(name):
        for source in task.sources:
            if source.chat_id:
                return source.chat_id
    return message.chat.id


def _author_name(message: Message) -> str:
    user = message.from_user
    return (user.username or user.full_name) if user else "участник"


@router.message(Command("start", "help", "join"))
async def cmd_help(message: Message, c: AppContainer) -> None:
    _member_from_message(message, c)
    suffix = "\n✅ Личные сообщения включены: сюда будут приходить подробные уведомления." if _is_private(message) else ""
    await message.answer(HELP + suffix, reply_markup=kb.introduce_keyboard())


@router.message(Command("mode"))
async def cmd_mode(message: Message, command: CommandObject, c: AppContainer) -> None:
    chat_id = message.chat.id
    arg = (command.args or "").strip().lower()
    if arg in {"auto", "авто", "on", "true", "1"}:
        c.mode.set_auto(chat_id, True)
    elif arg in {"manual", "ручной", "off", "false", "0", "confirm"}:
        c.mode.set_auto(chat_id, False)
    state = "🚀 АВТО" if c.mode.is_auto(chat_id) else "🛡️ С подтверждением"
    await message.answer(
        f"⚙️ <b>Режим задач:</b> {state}\n\n"
        "<code>/mode auto</code> — создавать сразу\n"
        "<code>/mode manual</code> — сначала спрашивать подтверждение"
    )


@router.message(Command("board"))
async def cmd_board(message: Message, c: AppContainer) -> None:
    cards = await c.board.list_cards()
    await message.answer(tx.render_board(cards))


@router.message(Command("tasks"))
async def cmd_tasks(message: Message, c: AppContainer) -> None:
    member = _member_from_message(message, c)
    name = member.username or member.full_name
    tasks = c.repo.open_by_assignee(name) if name else []
    await message.answer(tx.render_tasks(tasks))


@router.message(Command("profile", "cabinet"))
async def cmd_profile(message: Message, c: AppContainer) -> None:
    member = _member_from_message(message, c)
    await message.answer(c.cabinet.render_profile(member))


@router.message(Command("note"))
async def cmd_note(message: Message, command: CommandObject, c: AppContainer) -> None:
    member = _member_from_message(message, c)
    note_text = (command.args or "").strip()
    if not note_text:
        await message.answer("🗒️ Напишите заметку после команды: <code>/note созвониться с дизайнером</code>")
        return
    note = c.cabinet.add_note(member, note_text)
    await message.answer(f"🗒️ <b>Заметка сохранена</b>\n#{note.id} · {esc(note.text)}")


@router.message(Command("notes"))
async def cmd_notes(message: Message, c: AppContainer) -> None:
    member = _member_from_message(message, c)
    await message.answer(c.cabinet.render_notes(member))


@router.message(Command("kb", "knowledge"))
async def cmd_kb(message: Message, command: CommandObject, c: AppContainer) -> None:
    args = (command.args or "").strip()
    author = _author_name(message)
    if not args:
        await message.answer(c.cabinet.render_knowledge(c.cabinet.recent_knowledge()))
        return

    action, _, rest = args.partition(" ")
    if action.lower() in {"add", "добавь", "добавить"}:
        title, sep, body = rest.partition("|")
        if not sep or not body.strip():
            await message.answer("📚 Формат: <code>/kb add Заголовок | полезный текст</code>")
            return
        item = c.cabinet.add_knowledge(title, body, author)
        await message.answer(f"📚 <b>Добавил в базу знаний</b>\n#{item.id} · {esc(item.title)}")
        return
    if action.lower() in {"find", "search", "найди", "поиск"}:
        await message.answer(c.cabinet.render_knowledge(c.cabinet.search_knowledge(rest)))
        return
    await message.answer(c.cabinet.render_knowledge(c.cabinet.search_knowledge(args)))


@router.message(Command("register"))
async def cmd_register(message: Message, command: CommandObject, c: AppContainer) -> None:
    user = message.from_user
    args = (command.args or "").strip()
    full_name = user.full_name if user else "участник"
    aliases: list[str] = []
    if args:
        parts = args.split(";", 1)
        full_name = parts[0].strip() or full_name
        if len(parts) > 1:
            aliases = [a.strip() for a in parts[1].split(",") if a.strip()]
    member = c.team.register(
        TeamMember(
            user_id=user.id if user else None,
            username=user.username if user else None,
            full_name=full_name,
            aliases=aliases,
            dm_chat_id=message.chat.id if _is_private(message) else None,
        )
    )
    await message.answer(
        "✅ <b>Профиль обновлён</b>\n"
        f"👤 {esc(member.full_name or full_name)}"
        + (f" · @{esc(member.username)}" if member.username else "")
        + (f"\n🏷️ Алиасы: {esc(', '.join(member.aliases))}" if member.aliases else "")
    )


@router.message(Command("report"))
async def cmd_report(message: Message, command: CommandObject, c: AppContainer) -> None:
    member = _member_from_message(message, c)
    text = (command.args or "").strip()
    if not text:
        await message.answer(
            "🌙 Напишите отчёт после команды, например:\n"
            "<code>/report авторизацию сделал, макет ещё в работе</code>"
        )
        return
    key = member.username or member.full_name
    c.cabinet.record_report(member)
    notes = await c.reconciliation.record_report(_report_chat_id(message, c, member), key, text)
    if notes:
        await message.answer("🌙 <b>Отчёт принят, доска обновлена</b>\n" + "\n".join(esc(n) for n in notes))
    else:
        await message.answer("🌙 <b>Отчёт принят</b>\nПодходящих открытых задач для авто-статуса не нашёл.")


@router.message(Command("reconcile"))
async def cmd_reconcile(message: Message, c: AppContainer) -> None:
    digest, _ = c.reconciliation.evening_digest(message.chat.id)
    await message.answer(digest)


@router.message(Command("remind"))
async def cmd_remind(message: Message, c: AppContainer) -> None:
    from ...scheduler.jobs import run_reminders

    if c.bot is None:
        c.bot = message.bot
    sent = await run_reminders(c)
    if sent == 0:
        await message.answer("✅ Дедлайны под контролем: напоминать сейчас не о чем.")


@router.message(Command("whoami"))
async def cmd_whoami(message: Message, c: AppContainer) -> None:
    member = _member_from_message(message, c)
    dm = "включены" if member.dm_chat_id else "не включены"
    await message.answer(
        "👤 <b>Как я вас вижу</b>\n"
        f"Имя: {esc(member.full_name or '—')}\n"
        f"Username: @{esc(member.username or '—')}\n"
        f"Алиасы: {esc(', '.join(member.aliases) or '—')}\n"
        f"Личные уведомления: {dm}"
    )
