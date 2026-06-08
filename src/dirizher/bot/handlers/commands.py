"""Команды бота: справка, режимы, доска, профиль, заметки и база знаний."""

from __future__ import annotations

import re as _re
from html import escape as esc

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ...container import AppContainer
from ...domain.enums import TaskStatus
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
/profile — мой игровой профиль: XP, уровень, ачивки 🎮
/leaderboard — рейтинг команды по очкам 🏆
/report текст — вечерний отчёт (бот сам проставит статусы)
/reconcile — показать вечернюю сверку сейчас
/remind — проверить дедлайны и напомнить
/sync — сверить память с доской (убрать «призраков»)
/join — представиться (чтобы я мог вешать на вас задачи)
/register Имя; алиас1, алиас2 — представиться с алиасами
/alias энди, стеф — заменить свои прозвища (для тёзок)
/whoami — как я вас вижу
/enroll_voice — запомнить мой голос (подпись реплик на встречах)
/meeting_stop — остановить запись встречи
/forget — забыть всех участников (сброс памяти команды)
/help — эта справка

🎤 Кинь в чат ссылку Яндекс.Телемоста — я начну писать звук встречи, а в конце
пришлю саммари и вынесу задачи на доску.

Совет: нажмите «👋 Представиться» — так я свяжу ваше имя с аккаунтом.
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


_MENTION_RE = _re.compile(r"@([A-Za-z0-9_]{3,})")


def _card_belongs_to(card, member) -> bool:
    """Карточка назначена на участника? Сначала по id доски (точно), затем по имени."""
    if member is None:
        return False
    # точное совпадение по привязке к пользователю YouGile
    if member.yougile_id and member.yougile_id in getattr(card, "assignee_ids", []):
        return True
    # запасной вариант по имени — пока человек не привязал email к доске
    if card.assignee:
        who = card.assignee.lower()
        cands = [member.username or "", member.full_name, *member.aliases]
        if member.full_name:
            cands.append(member.full_name.split()[0])
        return any(cand and cand.lower() in who for cand in cands)
    return False


def _resolve_target(c: AppContainer, text: str, author) -> tuple[object | None, str, bool]:
    """Чьи задачи показывать: упомянутый @username или сам автор.

    Возвращает (участник|None, подпись, это_я). None — когда цель неизвестна
    (чтобы НЕ показывать чужие задачи всем подряд).
    """
    m = _MENTION_RE.search(text or "")
    if m:
        member = c.team.resolve(m.group(1))
        if member:
            label = f"@{member.username}" if member.username else (member.full_name or m.group(1))
            return member, label, False
        return None, m.group(1), False  # упомянут неизвестный — его задач не знаем
    if author is not None:
        # автор всегда известен (регистрируем на лету), матч по имени/привязке
        member = c.team.register(
            TeamMember(user_id=author.id, username=author.username, full_name=author.full_name)
        )
        return member, "вами", True
    return None, "", False


async def send_my_tasks(message: Message, c: AppContainer, *, query_text: str = "") -> None:
    """Показать задачи (мои или указанного @username) — по одной с кнопками.

    Источник истины — доска YouGile (переживает перезапуски). Чужие задачи в общий
    чат не вываливаем: если цель не определена — просим уточнить/представиться.
    """
    target, label, is_self = _resolve_target(c, query_text, message.from_user)
    if target is None:
        if label:  # упомянули неизвестного участника
            await message.answer(f"Не знаю участника «{label}» 🤔 Пусть он представится через /start.")
        else:
            await message.answer(
                "Не понял, чьи задачи показать. Представьтесь через /start → «Представиться» "
                "или уточните: <code>таски @username</code>."
            )
        return

    cards = await c.board.list_cards()
    mine = [c_ for c_ in cards if c_.status is not TaskStatus.done and _card_belongs_to(c_, target)]

    whose = "У вас" if is_self else f"У {label}"
    if not mine:
        await message.answer(f"{whose} нет открытых задач 🎉")
        return

    title = "Ваши открытые задачи" if is_self else f"Открытые задачи — {label}"
    await message.answer(f"<b>{title}:</b>")
    for card in mine[:20]:
        await message.answer(
            tx.render_board_task(card),
            reply_markup=kb.board_task_keyboard(card.id, card.status),
        )


@router.message(Command("tasks"))
async def cmd_tasks(message: Message, c: AppContainer) -> None:
    await send_my_tasks(message, c)


@router.message(Command("profile", "cabinet", "me", "profil", "профиль"))
async def cmd_profile(message: Message, command: CommandObject, c: AppContainer) -> None:
    """Единый личный кабинет: задачи/метрики + игровой профиль XP."""
    member = _member_from_message(message, c)
    arg = (command.args or "").strip()
    target = arg or member.username or member.full_name
    await message.answer(c.cabinet.render_profile(member) + "\n\n" + c.game.render_profile(target))


@router.message(Command("leaderboard", "top", "leaders", "рейтинг", "топ"))
async def cmd_leaderboard(message: Message, c: AppContainer) -> None:
    await message.answer(c.game.render_leaderboard())


@router.message(Command("game_reset", "leaderboard_reset", "сброс_очков"))
async def cmd_game_reset(message: Message, c: AppContainer) -> None:
    """Обнулить лидерборд (убрать тестовые/устаревшие профили)."""
    n = c.game.reset()
    await message.answer(
        f"🧹 Лидерборд обнулён (удалено профилей: {n}). Очки начнут копиться заново "
        f"по мере закрытия задач."
    )


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
    c.persist()
    await message.answer(
        "✅ <b>Профиль обновлён</b>\n"
        f"👤 {esc(member.full_name or full_name)}"
        + (f" · @{esc(member.username)}" if member.username else "")
        + (f"\n🏷️ Алиасы: {esc(', '.join(member.aliases))}" if member.aliases else "")
    )


@router.message(Command("alias", "aliases", "алиас", "алиасы"))
async def cmd_alias(message: Message, command: CommandObject, c: AppContainer) -> None:
    """Заменить свои прозвища (алиасы). Решает коллизию тёзок: «Андрей» у двоих.

    /alias — показать текущие
    /alias энди, стеф — заменить список (через запятую)
    """
    user = message.from_user
    me = c.team.register(
        TeamMember(user_id=user.id, username=user.username, full_name=user.full_name)
    )
    args = (command.args or "").strip()
    if not args:
        cur = ", ".join(me.aliases) or "— нет"
        await message.answer(
            f"Ваши прозвища: <b>{esc(cur)}</b>\n"
            f"Заменить: <code>/alias энди, стеф</code> · убрать все: <code>/alias -</code>"
        )
        return

    new_aliases = [] if args in {"-", "—", "нет"} else [a.strip() for a in args.split(",") if a.strip()]
    me.aliases = new_aliases
    c.persist()

    # Предупреждаем о коллизиях: тот же алиас есть у другого участника
    clashes: list[str] = []
    for a in new_aliases:
        others = [m for m in c.team.resolve_all(a) if m.user_id != user.id]
        if others:
            who = ", ".join("@" + (m.username or m.full_name) for m in others)
            clashes.append(f"«{a}» — также у {who}")
    text = f"✅ Прозвища обновлены: <b>{esc(', '.join(new_aliases) or '— нет')}</b>"
    if clashes:
        text += "\n⚠️ Совпадения (задача может уйти не тому): " + "; ".join(esc(c_) for c_ in clashes)
    await message.answer(text)


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


@router.message(Command("sync", "sync_board", "синхронизация"))
async def cmd_sync(message: Message, c: AppContainer) -> None:
    """Сверить память с доской: убрать «призраков» (удалённые на доске карточки)."""
    removed = await c.service.reconcile_with_board()
    c.persist()
    if removed:
        await message.answer(
            f"🧹 Синхронизировал с доской: убрал из памяти {removed} призрачных задач "
            f"(их карточек на доске уже нет). Теперь счётчики честные."
        )
    else:
        await message.answer("✅ Память и доска уже совпадают — призраков нет.")


@router.message(Command("remind"))
async def cmd_remind(message: Message, c: AppContainer) -> None:
    from ...scheduler.jobs import run_reminders

    if c.bot is None:
        c.bot = message.bot
    sent = await run_reminders(c)
    if sent == 0:
        await message.answer("✅ Дедлайны под контролем: напоминать сейчас не о чем.")


@router.message(Command("forget", "reset_team"))
async def cmd_forget(message: Message, c: AppContainer) -> None:
    count = len(c.team.all())
    if count == 0:
        await message.answer("Память о команде уже пуста — забывать некого 🙂")
        return
    await message.answer(
        f"⚠️ Забыть <b>всех участников</b> ({count})? Сотру имена, прозвища, "
        f"email и привязки к доске.\n"
        f"Задачи останутся — они хранятся на доске YouGile.",
        reply_markup=kb.forget_keyboard(),
    )


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
