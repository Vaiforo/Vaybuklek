"""Команды бота: справка, режимы, доска, профиль, заметки и база знаний."""

from __future__ import annotations

import re as _re
from datetime import date
from html import escape as esc

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ...container import AppContainer
from ...domain.enums import TaskStatus
from ...domain.models import Team, TeamMember
from ...permissions import (
    can_change_task_status,
    can_delete_task,
    can_manage_knowledge,
    can_manage_task,
    can_view_member_tasks,
    is_superuser,
)
from .. import keyboards as kb
from .. import text as tx

router = Router(name="commands")

DIVIDER = tx.DIVIDER

HELP = f"""\
🎼 <b>Дирижёр</b> — AI-помощник проджект-менеджера
{DIVIDER}
Просто пишите задачи в чат — я сам разберу их на карточки.
А этими командами можно управлять вручную.

• /settings — настройки уведомлений (задачи в личку, геймификация)
• /dm_notify <code>on|off</code> — личные уведомления о задачах в ЛС

📋 <b>Задачи и доска</b>
• /board — канбан-доска целиком
• /tasks <code>[@кто]</code> — открытые задачи (свои или коллеги)
• /unassigned_tasks — задачи без исполнителя
• /task_edit <code>ID …</code> — поправить карточку
• /task_del <code>ID</code> — убрать в корзину
• /trash · /task_restore <code>ID</code> — корзина и возврат

🔄 <b>Процесс</b>
• /mode <code>auto|manual</code> — создавать сразу или с подтверждением
• /report <code>…</code> — отчёт о прогрессе, доска обновится сама
• /digest — кто чем занят прямо сейчас
• /reconcile — вечерняя сверка по задачам
• /remind — разослать напоминания о дедлайнах
• /sync — сверить память с доской

📚 <b>Ещё разделы</b>
• /help_profile — профиль, заметки, рейтинг
• /help_kb — база знаний
• /help_meetings — встречи и голос
• /help_admin — администрирование
"""

HELP_MEETINGS = f"""\
🎙️ <b>Встречи: запись и распознавание</b>
{DIVIDER}
Кидаете в чат ссылку <code>telemost.yandex.ru/…</code> — я записываю созвон,
делаю саммари и выношу задачи на доску. Запись сама стопится по тишине (~3 мин)
или командой /meeting_stop.

<b>Команды</b>
• /meeting_source — показать/сменить источник звука
• /meeting_source <code>telemost|loopback|extension</code> — выбрать источник
• /meeting_capture — настроить запись из браузерного расширения
• /meeting_stop — остановить текущую запись

<b>Три источника звука</b>
🌐 <code>telemost</code> — бот сам заходит в звонок по ссылке (браузер Playwright).
🎧 <code>loopback</code> — пишу системный звук машины, что уже в звонке.
🧩 <code>extension</code> — звук шлёт браузерное расширение (захват вкладки созвона).

<b>Запись из браузера (расширение)</b>
1. /meeting_capture — пришлю конфиг (адрес, chat_id, токен) и включу режим.
2. Поставьте расширение из папки <code>extension/</code>
   (<code>chrome://extensions</code> → Загрузить распакованное), вставьте конфиг.
3. Включите в настройках «Записывать и мой микрофон», если ваш голос тоже нужен.
4. Кидаете ссылку Телемоста — расширение запишет вкладку само.

<b>Голос участников</b>
• /enroll_voice — запомнить мой голос, чтобы подписывать реплики на встречах
• /who <code>Speaker_1 Имя</code> — подписать голос спикера из последней записи
"""

HELP_PROFILE = f"""\
👤 <b>Личный кабинет</b>
{DIVIDER}
<b>Профиль</b>
• /profile — мои задачи, метрики и XP
• /leaderboard — рейтинг команды
• /whoami — как я вас вижу

<b>Заметки</b>
• /note <code>текст</code> — быстрая заметка
• /notes — список заметок
• /note_edit <code>ID текст</code> · /note_del <code>ID</code> — правка и удаление
• /notes_clear — очистить все заметки

<b>Кто я</b>
• /register <code>Имя; алиас1, алиас2</code> — представиться
• /alias <code>энди, стеф</code> — задать прозвища

<b>Уведомления</b>
• /settings — личные настройки уведомлений
• /dm_notify <code>on|off</code> — присылать в ЛС подтверждённые/изменённые задачи
"""

HELP_KB = f"""\
📚 <b>База знаний</b>
{DIVIDER}
• /kb — последние записи
• /kb_find <code>запрос</code> — поиск
• /kb_add <code>Заголовок | текст</code> — добавить
• /kb_edit <code>ID Заголовок | текст</code> — изменить
• /kb_del <code>ID</code> — удалить
• /kb_clear — очистить всю базу (только суперюзер)
"""

HELP_ADMIN = f"""\
🛠️ <b>Администрирование</b>
{DIVIDER}
<b>Доступы</b>
• /make_me_superuser — стать первым суперюзером (команда исчезнет после первого вызова)
• /grant_superuser <code>@кто</code> — назначить суперюзера

<b>Команды</b>
• /team_create <code>Название</code> — создать команду
• /team_add_member <code>TEAM @кто</code> — добавить участника
• /team_add_manager <code>TEAM @кто</code> — назначить руководителя
• /team_del_manager <code>TEAM @кто</code> — снять руководителя команды
• /team_del_member <code>TEAM @кто</code> — удалить участника из команды
• /no_team_manager <code>@кто</code> — руководитель без команды
• /del_manager <code>@кто</code> — снять руководителя без команды
• /teams — список команд с руководителями и участниками

<b>Опасная зона</b>
• /board_clear — очистить доску
• /reset_bot — полный сброс (кроме суперюзеров)
• /hard_reset — полный сброс, включая суперюзеров
• /forget — забыть всех участников
"""


def _visible_help(c: AppContainer) -> str:
    return HELP


def _visible_admin_help(c: AppContainer) -> str:
    base = HELP_ADMIN
    if c.team.superuser_exists():
        base = base.replace(
            "• /make_me_superuser — стать первым суперюзером (команда исчезнет после первого вызова)\n",
            "",
        )
    return base


def _actor(message: Message, c: AppContainer) -> TeamMember:
    return _member_from_message(message, c)


def _resolve_mentioned_member(c: AppContainer, message: Message, raw: str) -> TeamMember | None:
    raw = raw.strip()
    if raw.startswith("@"):
        return c.team.resolve(raw)
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        return c.team.register(TeamMember(user_id=u.id, username=u.username, full_name=u.full_name))
    return c.team.resolve(raw)


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


def _task_scope_chat_id(message: Message) -> int | None:
    """Разделение задач по чатам ОТКЛЮЧЕНО: всегда показываем задачи из всех чатов.

    Раньше в группе возвращался id чата (только задачи этой беседы), а в ЛС — None
    (все задачи). Теперь всегда None: и в группах, и в ЛС видны любые задачи.
    Машинерия скоупа (in_chat/_card_in_scope) сохранена — вернуть поведение можно,
    снова отдав `message.chat.id` для групп.
    """
    return None


def _task_in_scope(c: AppContainer, task, chat_id: int | None) -> bool:
    return c.repo.in_chat(task, chat_id)


def _card_in_scope(c: AppContainer, card, chat_id: int | None) -> bool:
    if chat_id is None:
        return True
    task = c.repo.get_by_card(card.id)
    return task is not None and _task_in_scope(c, task, chat_id)


def _find_task(c: AppContainer, raw_id: str):
    raw_id = (raw_id or "").strip()
    return c.repo.get(raw_id) or c.repo.get_by_card(raw_id)


def _tasks_for_member(c: AppContainer, member: TeamMember, *, chat_id: int | None = None):
    names = [member.username or "", member.full_name or "", *member.aliases]
    tasks = []
    seen: set[str] = set()
    for name in names:
        if not name:
            continue
        for task in c.repo.open_by_assignee_in_chat(name, chat_id):
            if task.id not in seen:
                seen.add(task.id)
                tasks.append(task)
    return tasks


def _render_current_digest(c: AppContainer) -> str:
    lines = ["🧭 <b>Кто чем занят сейчас</b>", "━━━━━━━━━━━━━━"]
    members = [m for m in c.team.all() if m.user_id is not None]
    if not members:
        return "🧭 Команда пока не зарегистрирована."
    for member in sorted(members, key=lambda m: (m.full_name or m.username or "").lower()):
        tasks = _tasks_for_member(c, member)
        active = [t for t in tasks if t.status is TaskStatus.in_progress]
        name = member.mention()
        if not tasks:
            lines.append(f"👤 {name} — задач в работе нет")
            continue
        if not active:
            lines.append(f"👤 {name} — нет выполняемых сейчас задач · задач в работе: <b>{len(tasks)}</b>")
            continue
        lines.append(f"👤 {name} — выполняет сейчас: <b>{len(active)}</b> · всего в работе: <b>{len(tasks)}</b>")
        for task in active[:5]:
            lines.append(f"  • <b>{esc(task.title)}</b> · 📅 {esc(task.deadline_display())}")
    return "\n".join(lines)


def _render_trash(c: AppContainer) -> str:
    from datetime import datetime, timezone

    items = sorted(c.repo.trashed(), key=lambda t: t.delete_after or datetime.max.replace(tzinfo=timezone.utc))
    if not items:
        return "🗑️ <b>Корзина пуста</b>"
    now = datetime.now(timezone.utc)
    lines = ["🗑️ <b>Корзина задач</b>", "━━━━━━━━━━━━━━", "Восстановить: <code>/task_restore ID</code>", ""]
    for task in items:
        left = "—"
        if task.delete_after:
            seconds = max(0, int((task.delete_after - now).total_seconds()))
            left = f"{seconds // 3600}ч {(seconds % 3600) // 60}м"
        lines.append(f"• <code>{esc(task.id)}</code> · <b>{esc(task.title)}</b>")
        lines.append(f"  👤 {esc(task.assignee or '—')} · удалится через: <b>{esc(left)}</b>")
    return "\n".join(lines)


def _render_unassigned(c: AppContainer, *, chat_id: int | None) -> str:
    tasks = sorted(
        c.repo.open_unassigned(chat_id=chat_id),
        key=lambda t: (t.deadline is None, t.deadline or date.max, t.title.lower()),
    )
    scope = "во всех чатах" if chat_id is None else "в этой конфе"
    if not tasks:
        return f"📥 <b>Обезличенных задач {esc(scope)} нет</b>"
    lines = [f"📥 <b>Обезличенные задачи {esc(scope)}</b>", "━━━━━━━━━━━━━━"]
    for idx, task in enumerate(tasks[:30], start=1):
        status = tx.effective_status(task)
        team = c.team.get_team(task.team_id)
        team_name = team.name if team else "—"
        lines.append(f"{idx}. <code>{esc(task.id)}</code> · <b>{esc(task.title)}</b>")
        lines.append(f"   🔸 {esc(status.label_ru)} · 👥 Команда: {esc(team_name)}")
        lines.append(f"   📅 {esc(task.deadline_display())}")
    hidden = len(tasks) - 30
    if hidden > 0:
        lines.append(f"… ещё {hidden}")
    return "\n".join(lines)


def _author_name(message: Message) -> str:
    user = message.from_user
    return (user.username or user.full_name) if user else "участник"


@router.message(Command("start", "help", "join"))
async def cmd_help(message: Message, c: AppContainer) -> None:
    _member_from_message(message, c)
    suffix = "\n✅ Личные сообщения включены: сюда будут приходить подробные уведомления." if _is_private(message) else ""
    await message.answer(_visible_help(c) + suffix, reply_markup=kb.introduce_keyboard())


def _render_settings(member: TeamMember) -> str:
    dm_state = "✅ включены" if member.notify_assignment else "🔕 выключены"
    game_state = "✅ включены" if member.notify_gamification else "🔕 выключены"
    return (
        "⚙️ <b>Настройки уведомлений</b>\n"
        f"{DIVIDER}\n"
        "📨 Задачи в личку: <b>" + dm_state + "</b>\n"
        "Когда вам подтвердили или изменили задачу в чате — пришлю её карточку в "
        "личку. По умолчанию включено. Быстрый тумблер: /dm_notify on|off.\n\n"
        "🎮 Геймификация: <b>" + game_state + "</b>\n"
        "Начисление XP, повышение уровня и новые ачивки. По умолчанию выключено, "
        "чтобы не спамить личку."
    )


@router.message(Command("settings"))
async def cmd_settings(message: Message, c: AppContainer) -> None:
    member = _member_from_message(message, c)
    c.persist()
    await message.answer(_render_settings(member), reply_markup=kb.settings_keyboard(member))


@router.message(Command("dm_notify", "notify_dm"))
async def cmd_dm_notify(message: Message, command: CommandObject, c: AppContainer) -> None:
    """`/dm_notify [on|off]` — включить/выключить личные уведомления о задачах.

    Без аргумента — показать текущее состояние и подсказку. Настройка личная
    (на участника): выключивший не получает карточки подтверждённых/изменённых
    задач в ЛС, но в общем чате задачи показываются как обычно.
    """
    member = _member_from_message(message, c)
    arg = (command.args or "").strip().lower()
    on = {"on", "вкл", "включить", "да", "1", "true"}
    off = {"off", "выкл", "выключить", "нет", "0", "false"}
    if arg in on:
        member.notify_assignment = True
        c.persist()
        await message.answer("📨 Личные уведомления о задачах: <b>включены</b>.")
    elif arg in off:
        member.notify_assignment = False
        c.persist()
        await message.answer("🔕 Личные уведомления о задачах: <b>выключены</b>. Задачи в чате остаются.")
    else:
        now = "включены ✅" if member.notify_assignment else "выключены 🔕"
        await message.answer(
            f"📨 Личные уведомления о задачах сейчас <b>{now}</b>.\n"
            "Переключить: <code>/dm_notify on</code> или <code>/dm_notify off</code>."
        )


@router.message(Command("help_meetings"))
async def cmd_help_meetings(message: Message, c: AppContainer) -> None:
    _member_from_message(message, c)
    await message.answer(HELP_MEETINGS)


@router.message(Command("help_profile"))
async def cmd_help_profile(message: Message, c: AppContainer) -> None:
    _member_from_message(message, c)
    await message.answer(HELP_PROFILE)


@router.message(Command("help_kb"))
async def cmd_help_kb(message: Message, c: AppContainer) -> None:
    _member_from_message(message, c)
    await message.answer(HELP_KB)


@router.message(Command("help_admin"))
async def cmd_help_admin(message: Message, c: AppContainer) -> None:
    _member_from_message(message, c)
    await message.answer(_visible_admin_help(c))


@router.message(Command("make_me_superuser"))
async def cmd_make_me_superuser(message: Message, c: AppContainer) -> None:
    member = _actor(message, c)
    if not c.team.make_superuser_once(member):
        return
    c.persist()
    await message.answer("👑 Готово: вы первый суперюзер. Команда /make_me_superuser больше не будет показываться в /help.")


@router.message(Command("grant_superuser", "make_superuser"))
async def cmd_grant_superuser(message: Message, command: CommandObject, c: AppContainer) -> None:
    actor = _actor(message, c)
    if not is_superuser(actor):
        return
    target = _resolve_mentioned_member(c, message, (command.args or "").strip())
    if target is None:
        await message.answer("Формат: <code>/grant_superuser @username</code> или ответьте командой на сообщение участника.")
        return
    c.team.grant_superuser(target)
    c.persist()
    await message.answer(f"👑 Суперюзер назначен: {target.mention()}.")


@router.message(Command("no_team_manager", "add_manager", "manager_no_team", "make_no_team_manager"))
async def cmd_no_team_manager(message: Message, command: CommandObject, c: AppContainer) -> None:
    actor = _actor(message, c)
    if not is_superuser(actor):
        return
    target = _resolve_mentioned_member(c, message, (command.args or "").strip())
    if target is None:
        await message.answer(
            "Формат: <code>/no_team_manager @username</code> или <code>/add_manager @username</code> "
            "или ответьте командой на сообщение участника."
        )
        return
    c.team.grant_no_team_manager(target)
    c.persist()
    await message.answer(
        f"🧭 Руководитель без команды назначен: {target.mention()}. "
        "В графе «Команда» у него остаётся <b>—</b>."
    )


@router.message(Command("del_manager"))
async def cmd_del_manager(message: Message, command: CommandObject, c: AppContainer) -> None:
    actor = _actor(message, c)
    if not is_superuser(actor):
        return
    target = _resolve_mentioned_member(c, message, (command.args or "").strip())
    if target is None:
        await message.answer("Формат: <code>/del_manager @username</code> или ответьте командой на сообщение участника.")
        return
    c.team.revoke_no_team_manager(target)
    c.persist()
    await message.answer(f"🧭 Руководитель без команды снят: {target.mention()}.")


@router.message(Command("team_create"))
async def cmd_team_create(message: Message, command: CommandObject, c: AppContainer) -> None:
    actor = _actor(message, c)
    if not is_superuser(actor):
        return
    name = (command.args or "").strip()
    if not name:
        await message.answer("Формат: <code>/team_create Название</code>")
        return
    team = c.team.add_team(Team(name=name[:80]))
    c.persist()
    await message.answer(f"👥 Команда создана: <b>{esc(team.name)}</b> · <code>{team.id}</code>")


@router.message(Command("team_add_member"))
async def cmd_team_add_member(message: Message, command: CommandObject, c: AppContainer) -> None:
    actor = _actor(message, c)
    if not is_superuser(actor):
        return
    raw_team, _, raw_user = (command.args or "").strip().partition(" ")
    team = c.team.get_team(raw_team)
    target = _resolve_mentioned_member(c, message, raw_user)
    if team is None or target is None:
        await message.answer("Формат: <code>/team_add_member TEAM @username</code>")
        return
    c.team.assign_member_to_team(target, team, leader=False)
    c.persist()
    await message.answer(f"👤 Участник добавлен в <b>{esc(team.name)}</b>: {target.mention()}.")


@router.message(Command("team_add_manager"))
async def cmd_team_add_manager(message: Message, command: CommandObject, c: AppContainer) -> None:
    actor = _actor(message, c)
    if not is_superuser(actor):
        return
    raw_team, _, raw_user = (command.args or "").strip().partition(" ")
    team = c.team.get_team(raw_team)
    target = _resolve_mentioned_member(c, message, raw_user)
    if team is None or target is None:
        await message.answer("Формат: <code>/team_add_manager TEAM @username</code>")
        return
    c.team.assign_member_to_team(target, team, leader=True)
    c.persist()
    await message.answer(f"🧭 Руководитель команды <b>{esc(team.name)}</b>: {target.mention()}.")


@router.message(Command("team_del_manager"))
async def cmd_team_del_manager(message: Message, command: CommandObject, c: AppContainer) -> None:
    actor = _actor(message, c)
    if not is_superuser(actor):
        return
    raw_team, _, raw_user = (command.args or "").strip().partition(" ")
    team = c.team.get_team(raw_team)
    target = _resolve_mentioned_member(c, message, raw_user)
    if team is None or target is None:
        await message.answer("Формат: <code>/team_del_manager TEAM @username</code>")
        return
    c.team.remove_member_from_team(target, team, leader=True)
    c.persist()
    await message.answer(f"🧭 Руководитель команды <b>{esc(team.name)}</b> снят: {target.mention()}.")


@router.message(Command("team_del_member"))
async def cmd_team_del_member(message: Message, command: CommandObject, c: AppContainer) -> None:
    actor = _actor(message, c)
    if not is_superuser(actor):
        return
    raw_team, _, raw_user = (command.args or "").strip().partition(" ")
    team = c.team.get_team(raw_team)
    target = _resolve_mentioned_member(c, message, raw_user)
    if team is None or target is None:
        await message.answer("Формат: <code>/team_del_member TEAM @username</code>")
        return
    c.team.remove_member_from_team(target, team, leader=False)
    c.persist()
    await message.answer(f"👤 Участник удалён из <b>{esc(team.name)}</b>: {target.mention()}.")


def _mentions(members: list[TeamMember | None]) -> str:
    people = [m.mention() for m in members if m is not None]
    return ", ".join(people) if people else "—"


@router.message(Command("teams"))
async def cmd_teams(message: Message, c: AppContainer) -> None:
    teams = c.team.teams()
    no_team_managers = [m for m in c.team.all() if m.is_no_team_manager]
    if not teams and not no_team_managers:
        await message.answer("Команды ещё не созданы. Суперюзер может создать: <code>/team_create Название</code>")
        return
    lines = ["👥 <b>Команды</b>", DIVIDER]
    if no_team_managers:
        lines.extend([
            "🧭 <b>Команда —</b>",
            "   👑 Руководители: " + _mentions(no_team_managers),
            "   👤 Участники: —",
            "",
        ])
    for t in teams:
        managers = [c.team.get_by_user_id(uid) for uid in t.manager_user_ids]
        members = [c.team.get_by_user_id(uid) for uid in t.member_user_ids]
        lines.extend([
            f"🏷️ <b>{esc(t.name)}</b> · <code>{esc(t.id)}</code>",
            "   👑 Руководители: " + _mentions(managers),
            "   👤 Участники: " + _mentions(members),
            "",
        ])
    await message.answer("\n".join(lines).rstrip())


@router.message(Command("board_clear", "clear_board"))
async def cmd_board_clear(message: Message, command: CommandObject, c: AppContainer) -> None:
    actor = _actor(message, c)
    if not is_superuser(actor):
        return
    if (command.args or "").strip().lower() not in {"confirm", "yes", "да", "подтверждаю"}:
        await message.answer("⚠️ Очистить всю канбан-доску? Подтвердите: <code>/board_clear confirm</code>")
        return
    cards = await c.board.list_cards()
    for card in cards:
        await c.board.delete_card(card.id)
    for task in c.repo.all():
        c.memory.forget(task.id)
    c.repo.clear()
    c.persist()
    await message.answer(f"🧹 Канбан-доска очищена. Удалено карточек: <b>{len(cards)}</b>.")


async def _reset_bot_state(c: AppContainer, *, keep_superusers: bool) -> tuple[int, int, int]:
    """Очистить задачи, доску, кабинеты и реестр пользователей.

    Возвращает (задач удалено, карточек удалено, участников забыто).
    `keep_superusers=True` оставляет только суперюзеров; `False` чистит всех,
    чтобы после hard reset первым суперюзером можно было назначиться заново.
    """
    cards = await c.board.list_cards()
    for card in cards:
        await c.board.delete_card(card.id)
    for task in c.repo.all():
        c.memory.forget(task.id)
    tasks = c.repo.clear()
    c.cabinet.clear_knowledge()
    for member in list(c.team.all()):
        c.cabinet.clear_notes(member)
    forgotten = c.team.clear(keep_superusers=keep_superusers, clear_teams=True)
    c.game.reset()
    c.persist()
    return tasks, len(cards), forgotten


@router.message(Command("reset_bot", "bot_clear"))
async def cmd_reset_bot(message: Message, command: CommandObject, c: AppContainer) -> None:
    actor = _actor(message, c)
    if not is_superuser(actor):
        return
    if (command.args or "").strip().lower() not in {"confirm", "yes", "да", "подтверждаю"}:
        await message.answer("⚠️ Полностью очистить задачи, доску, знания, заметки и всех участников кроме суперюзеров? Подтвердите: <code>/reset_bot confirm</code>")
        return
    tasks, cards, forgotten = await _reset_bot_state(c, keep_superusers=True)
    await message.answer(f"🧹 Бот очищен: задач {tasks}, карточек {cards}, забыто участников {forgotten}. Суперюзеры сохранены.")


@router.message(Command("hard_reset"))
async def cmd_hard_reset(message: Message, command: CommandObject, c: AppContainer) -> None:
    actor = _actor(message, c)
    if not is_superuser(actor):
        return
    if (command.args or "").strip().lower() not in {"confirm", "yes", "да", "подтверждаю"}:
        await message.answer(
            "⚠️ Полностью очистить задачи, доску, знания, заметки и <b>всех пользователей, включая суперюзеров</b>? "
            "Подтвердите: <code>/hard_reset confirm</code>"
        )
        return
    tasks, cards, forgotten = await _reset_bot_state(c, keep_superusers=False)
    await message.answer(
        f"🧹 Жёсткий сброс выполнен: задач {tasks}, карточек {cards}, забыто участников {forgotten}. "
        "Суперюзеров больше нет — первого можно назначить через /make_me_superuser."
    )


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
    actor = _actor(message, c)
    scope_chat_id = _task_scope_chat_id(message)
    cards = await c.board.list_cards()
    trashed_cards = {t.board_card_id for t in c.repo.trashed() if t.board_card_id}
    cards = [card for card in cards if card.id not in trashed_cards and _card_in_scope(c, card, scope_chat_id)]
    if c.team.superuser_exists() and not is_superuser(actor):
        cards = [
            card for card in cards
            if (task := c.repo.get_by_card(card.id)) is not None
            and can_manage_task(actor, task, c.team)
        ]
        if not cards:
            return
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
    raw_text = (text or "").strip()
    m = _MENTION_RE.search(raw_text)
    if m:
        member = c.team.resolve(m.group(1))
        if member:
            label = f"@{member.username}" if member.username else (member.full_name or m.group(1))
            return member, label, False
        return None, m.group(1), False  # упомянут неизвестный — его задач не знаем
    if raw_text:
        token = raw_text.split()[0].lstrip("@")
        member = c.team.resolve(token)
        if member:
            label = f"@{member.username}" if member.username else (member.full_name or token)
            return member, label, False
        return None, token, False
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
    actor = _member_from_message(message, c)
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
    if not can_view_member_tasks(actor, target):
        return

    scope_chat_id = _task_scope_chat_id(message)
    cards = await c.board.list_cards()
    trashed_cards = {t.board_card_id for t in c.repo.trashed() if t.board_card_id}
    mine = [
        c_ for c_ in cards
        if c_.id not in trashed_cards
        and tx.effective_status(c_) is not TaskStatus.done
        and _card_belongs_to(c_, target)
        and _card_in_scope(c, c_, scope_chat_id)
    ]

    whose = "У вас" if is_self else f"У {label}"
    if not mine:
        await message.answer(f"{whose} нет открытых задач 🎉")
        return

    title = "Ваши открытые задачи" if is_self else f"Открытые задачи — {label}"
    await message.answer(f"<b>{title}:</b>")
    for card in mine[:20]:
        await message.answer(
            tx.render_board_task(card),
            reply_markup=(
                kb.board_task_keyboard(
                    card.id,
                    card.status,
                    allow_status=(task := c.repo.get_by_card(card.id)) is not None
                    and can_change_task_status(actor, task, c.team),
                    allow_delete=task is not None and can_delete_task(actor, task, c.team),
                )
                if (task := c.repo.get_by_card(card.id)) is not None
                and can_change_task_status(actor, task, c.team)
                else None
            ),
        )


@router.message(Command("digest", "current_digest", "занятость"))
async def cmd_current_digest(message: Message, c: AppContainer) -> None:
    actor = _actor(message, c)
    if not (is_superuser(actor) or actor.leader_team_ids or actor.is_no_team_manager):
        return
    await message.answer(_render_current_digest(c))


@router.message(Command("trash", "deleted_tasks", "корзина"))
async def cmd_trash(message: Message, c: AppContainer) -> None:
    actor = _actor(message, c)
    if not (is_superuser(actor) or actor.leader_team_ids or actor.is_no_team_manager):
        return
    await c.service.purge_expired_trash()
    await message.answer(_render_trash(c))


@router.message(Command("task_restore", "restore_task"))
async def cmd_task_restore(message: Message, command: CommandObject, c: AppContainer) -> None:
    actor = _actor(message, c)
    raw_id = (command.args or "").strip()
    task = c.repo.get(raw_id) or c.repo.get_by_card(raw_id)
    if task is None or task.trashed_at is None:
        await message.answer(f"Задача <code>{esc(raw_id)}</code> не найдена в корзине.")
        return
    if not can_delete_task(actor, task, c.team):
        return
    await c.service.restore_task(task)
    await message.answer(f"♻️ Восстановил задачу: «{esc(task.title)}».")


@router.message(Command("unassigned_tasks", "unassigned", "no_assignee", "без_исполнителя"))
async def cmd_unassigned_tasks(message: Message, c: AppContainer) -> None:
    actor = _actor(message, c)
    if c.team.superuser_exists() and not (is_superuser(actor) or actor.leader_team_ids or actor.is_no_team_manager):
        return
    await message.answer(_render_unassigned(c, chat_id=_task_scope_chat_id(message)))


@router.message(Command("tasks"))
async def cmd_tasks(message: Message, command: CommandObject, c: AppContainer) -> None:
    await send_my_tasks(message, c, query_text=(command.args or "").strip())


@router.message(Command("task_edit", "edit_task"))
async def cmd_task_edit(message: Message, command: CommandObject, c: AppContainer) -> None:
    actor = _actor(message, c)
    raw_id, _, correction = (command.args or "").strip().partition(" ")
    if not raw_id or not correction.strip():
        await message.answer("Формат: <code>/task_edit ID что изменить</code>")
        return
    task = _find_task(c, raw_id)
    if task is None:
        await message.answer(f"Задача <code>{esc(raw_id)}</code> не найдена.")
        return
    if task.trashed_at is not None:
        await message.answer("Задача в корзине. Сначала восстановите её через /task_restore.")
        return
    if not can_delete_task(actor, task, c.team):
        return
    await c.service.apply_correction(task, correction)
    await c.service.edit_task(task)
    await message.answer("✏️ <b>Задача обновлена</b>\n" + tx.render_task_card(task))
    # Уведомляем исполнителей об изменении в личку (если не выключено /dm_notify).
    from ..flow import notify_assignee_edited

    await notify_assignee_edited(message.bot, c, task, message.chat.id)


@router.message(Command("task_del", "delete_task"))
async def cmd_task_del(message: Message, command: CommandObject, c: AppContainer) -> None:
    actor = _actor(message, c)
    raw_id = (command.args or "").strip()
    if not raw_id:
        await message.answer("Формат: <code>/task_del ID</code>")
        return
    task = _find_task(c, raw_id)
    if task is None:
        await message.answer(f"Задача <code>{esc(raw_id)}</code> не найдена.")
        return
    if task.trashed_at is not None:
        await message.answer(f"Задача уже в корзине. Восстановить: <code>/task_restore {esc(task.id)}</code>")
        return
    if not can_delete_task(actor, task, c.team):
        return
    await c.service.soft_delete_task(task)
    c.persist()
    await message.answer(f"🗑️ Задача перемещена в корзину на 4 часа: «{esc(task.title)}». Восстановить: <code>/task_restore {esc(task.id)}</code>")


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
    if not is_superuser(_actor(message, c)):
        return
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
    await message.answer(
        "🗒️ <b>Заметка сохранена</b>\n"
        f"━━━━━━━━━━━━━━\n<b>#{note.id}</b>\n{esc(note.text)}"
    )


@router.message(Command("note_edit", "edit_note"))
async def cmd_note_edit(message: Message, command: CommandObject, c: AppContainer) -> None:
    member = _member_from_message(message, c)
    args = (command.args or "").strip()
    raw_id, _, text = args.partition(" ")
    if not raw_id.isdigit() or not text.strip():
        await message.answer("✏️ Формат: <code>/note_edit ID новый текст</code>")
        return
    note = c.cabinet.edit_note(member, int(raw_id), text)
    if note is None:
        await message.answer(f"🗒️ Заметка #{esc(raw_id)} не найдена.")
        return
    await message.answer(
        "✏️ <b>Заметка обновлена</b>\n"
        f"━━━━━━━━━━━━━━\n<b>#{note.id}</b>\n{esc(note.text)}"
    )


@router.message(Command("note_del", "delete_note", "note_delete"))
async def cmd_note_del(message: Message, command: CommandObject, c: AppContainer) -> None:
    member = _member_from_message(message, c)
    raw_id = (command.args or "").strip()
    if not raw_id.isdigit():
        await message.answer("🗑️ Формат: <code>/note_del ID</code>")
        return
    note = c.cabinet.delete_note(member, int(raw_id))
    if note is None:
        await message.answer(f"🗒️ Заметка #{esc(raw_id)} не найдена.")
        return
    await message.answer(
        "🗑️ <b>Заметка удалена</b>\n"
        f"━━━━━━━━━━━━━━\n<b>#{note.id}</b> · {esc(note.text)}"
    )


@router.message(Command("notes_clear", "clear_notes"))
async def cmd_notes_clear(message: Message, command: CommandObject, c: AppContainer) -> None:
    member = _member_from_message(message, c)
    arg = (command.args or "").strip().lower()
    total = len(c.cabinet.notes_for(member, limit=None))
    if arg not in {"confirm", "yes", "да", "подтверждаю"}:
        await message.answer(
            "🧹 <b>Очистить все ваши заметки?</b>\n"
            f"━━━━━━━━━━━━━━\nБудет удалено: <b>{total}</b>.\n"
            "Для подтверждения отправьте: <code>/notes_clear confirm</code>"
        )
        return
    removed = c.cabinet.clear_notes(member)
    await message.answer(f"🧹 <b>Заметки очищены</b>\n━━━━━━━━━━━━━━\nУдалено: <b>{removed}</b>.")


@router.message(Command("notes"))
async def cmd_notes(message: Message, c: AppContainer) -> None:
    member = _member_from_message(message, c)
    await message.answer(c.cabinet.render_notes(member))


@router.message(Command("kb_add"))
async def cmd_kb_add(message: Message, command: CommandObject, c: AppContainer) -> None:
    member = _actor(message, c)
    author = _author_name(message)
    team_id = member.member_team_ids[0] if member.member_team_ids else None
    title, sep, body = (command.args or "").strip().partition("|")
    if not sep or not body.strip():
        await message.answer("📚 Формат: <code>/kb_add Заголовок | полезный текст</code>")
        return
    item = c.cabinet.add_knowledge(title, body, author, author_user_id=member.user_id, team_id=team_id)
    await message.answer(
        "📚 <b>Добавил в базу знаний</b>\n"
        f"━━━━━━━━━━━━━━\n<b>#{item.id} · {esc(item.title)}</b>\n{esc(item.text)}"
    )


@router.message(Command("kb_find"))
async def cmd_kb_find(message: Message, command: CommandObject, c: AppContainer) -> None:
    await message.answer(c.cabinet.render_knowledge(c.cabinet.search_knowledge((command.args or "").strip())))


@router.message(Command("kb_edit"))
async def cmd_kb_edit(message: Message, command: CommandObject, c: AppContainer) -> None:
    member = _actor(message, c)
    author = _author_name(message)
    raw_id, _, payload = (command.args or "").strip().partition(" ")
    title, sep, body = payload.partition("|")
    if not raw_id.isdigit() or not sep or not body.strip():
        await message.answer("✏️ Формат: <code>/kb_edit ID Заголовок | новый текст</code>")
        return
    item = c.cabinet.get_knowledge(int(raw_id))
    if item is not None and not can_manage_knowledge(member, item.author_user_id, item.team_id):
        return
    item = c.cabinet.edit_knowledge(int(raw_id), title, body, author)
    if item is None:
        await message.answer(f"📚 Запись #{esc(raw_id)} не найдена.")
        return
    await message.answer(
        "✏️ <b>Запись БЗ обновлена</b>\n"
        f"━━━━━━━━━━━━━━\n<b>#{item.id} · {esc(item.title)}</b>\n{esc(item.text)}"
    )


@router.message(Command("kb_del"))
async def cmd_kb_del(message: Message, command: CommandObject, c: AppContainer) -> None:
    member = _actor(message, c)
    raw_id = (command.args or "").strip()
    if not raw_id.isdigit():
        await message.answer("🗑️ Формат: <code>/kb_del ID</code>")
        return
    item = c.cabinet.get_knowledge(int(raw_id))
    if item is not None and not can_manage_knowledge(member, item.author_user_id, item.team_id):
        return
    item = c.cabinet.delete_knowledge(int(raw_id))
    if item is None:
        await message.answer(f"📚 Запись #{esc(raw_id)} не найдена.")
        return
    await message.answer("🗑️ <b>Запись БЗ удалена</b>\n" f"━━━━━━━━━━━━━━\n<b>#{item.id} · {esc(item.title)}</b>")


@router.message(Command("kb_clear"))
async def cmd_kb_clear(message: Message, command: CommandObject, c: AppContainer) -> None:
    member = _actor(message, c)
    if not is_superuser(member):
        return
    confirm = (command.args or "").strip().lower()
    total = len(c.cabinet.recent_knowledge(limit=10_000))
    if confirm not in {"confirm", "yes", "да", "подтверждаю"}:
        await message.answer(
            "🧹 <b>Очистить всю базу знаний?</b>\n"
            f"━━━━━━━━━━━━━━\nБудет удалено записей: <b>{total}</b>.\n"
            "Для подтверждения отправьте: <code>/kb_clear confirm</code>"
        )
        return
    removed = c.cabinet.clear_knowledge()
    await message.answer(f"🧹 <b>База знаний очищена</b>\n━━━━━━━━━━━━━━\nУдалено записей: <b>{removed}</b>.")


@router.message(Command("kb", "knowledge"))
async def cmd_kb(message: Message, command: CommandObject, c: AppContainer) -> None:
    args = (command.args or "").strip()
    member = _actor(message, c)
    author = _author_name(message)
    team_id = member.member_team_ids[0] if member.member_team_ids else None
    if not args:
        await message.answer(c.cabinet.render_knowledge(c.cabinet.recent_knowledge()))
        return

    action, _, rest = args.partition(" ")
    if action.lower() in {"add", "добавь", "добавить"}:
        title, sep, body = rest.partition("|")
        if not sep or not body.strip():
            await message.answer("📚 Формат: <code>/kb_add Заголовок | полезный текст</code>")
            return
        item = c.cabinet.add_knowledge(title, body, author, author_user_id=member.user_id, team_id=team_id)
        await message.answer(
            "📚 <b>Добавил в базу знаний</b>\n"
            f"━━━━━━━━━━━━━━\n<b>#{item.id} · {esc(item.title)}</b>\n{esc(item.text)}"
        )
        return
    action_l = action.lower()
    if action_l in {"find", "search", "найди", "поиск"}:
        await message.answer(c.cabinet.render_knowledge(c.cabinet.search_knowledge(rest)))
        return
    if action_l in {"edit", "ред", "изменить"}:
        raw_id, _, payload = rest.partition(" ")
        title, sep, body = payload.partition("|")
        if not raw_id.isdigit() or not sep or not body.strip():
            await message.answer("✏️ Формат: <code>/kb_edit ID Заголовок | новый текст</code>")
            return
        item = c.cabinet.get_knowledge(int(raw_id))
        if item is not None and not can_manage_knowledge(member, item.author_user_id, item.team_id):
            return
        item = c.cabinet.edit_knowledge(int(raw_id), title, body, author)
        if item is None:
            await message.answer(f"📚 Запись #{esc(raw_id)} не найдена.")
            return
        await message.answer(
            "✏️ <b>Запись БЗ обновлена</b>\n"
            f"━━━━━━━━━━━━━━\n<b>#{item.id} · {esc(item.title)}</b>\n{esc(item.text)}"
        )
        return
    if action_l in {"del", "delete", "rm", "удалить"}:
        raw_id = rest.strip()
        if not raw_id.isdigit():
            await message.answer("🗑️ Формат: <code>/kb_del ID</code>")
            return
        item = c.cabinet.get_knowledge(int(raw_id))
        if item is not None and not can_manage_knowledge(member, item.author_user_id, item.team_id):
            return
        item = c.cabinet.delete_knowledge(int(raw_id))
        if item is None:
            await message.answer(f"📚 Запись #{esc(raw_id)} не найдена.")
            return
        await message.answer(
            "🗑️ <b>Запись БЗ удалена</b>\n"
            f"━━━━━━━━━━━━━━\n<b>#{item.id} · {esc(item.title)}</b>"
        )
        return
    if action_l in {"clear", "clean", "очистить", "чистить"}:
        if not is_superuser(member):
            return
        confirm = rest.strip().lower()
        total = len(c.cabinet.recent_knowledge(limit=10_000))
        if confirm not in {"confirm", "yes", "да", "подтверждаю"}:
            await message.answer(
                "🧹 <b>Очистить всю базу знаний?</b>\n"
                f"━━━━━━━━━━━━━━\nБудет удалено записей: <b>{total}</b>.\n"
                "Для подтверждения отправьте: <code>/kb_clear confirm</code>"
            )
            return
        removed = c.cabinet.clear_knowledge()
        await message.answer(f"🧹 <b>База знаний очищена</b>\n━━━━━━━━━━━━━━\nУдалено записей: <b>{removed}</b>.")
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
        # XP-строки (🎮) — в личку исполнителю; в чат только статусы задач.
        status = [n for n in notes if not n.startswith("🎮")]
        xp = [n for n in notes if n.startswith("🎮")]
        await message.answer("🌙 <b>Отчёт принят, доска обновлена</b>\n" + "\n".join(esc(n) for n in status))
        from ..flow import deliver_xp
        await deliver_xp(
            message.bot,
            c,
            xp,
            assignee=key,
            dm_chat_id=member.dm_chat_id if member else None,
            chat_id=message.chat.id,
        )
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
    sent = await run_reminders(c, respect_quiet=False)  # ручной вызов — тихие часы не мешают
    if sent == 0:
        await message.answer("✅ Дедлайны под контролем: напоминать сейчас не о чем.")


@router.message(Command("forget", "reset_team"))
async def cmd_forget(message: Message, c: AppContainer) -> None:
    if not is_superuser(_actor(message, c)):
        return
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
    teams = [c.team.get_team(tid) for tid in member.member_team_ids]
    team_names = ", ".join(t.name for t in teams if t) or "—"
    managed_teams = [c.team.get_team(tid) for tid in member.leader_team_ids]
    managed_names = ", ".join(t.name for t in managed_teams if t)
    if member.is_no_team_manager:
        managed_names = ", ".join([name for name in [managed_names, "нет команды"] if name])
    await message.answer(
        "👤 <b>Как я вас вижу</b>\n"
        f"Имя: {esc(member.full_name or '—')}\n"
        f"Username: @{esc(member.username or '—')}\n"
        f"Алиасы: {esc(', '.join(member.aliases) or '—')}\n"
        f"Команда: {esc(team_names)}\n"
        f"Руководит: {esc(managed_names or '—')}\n"
        f"Личные уведомления: {dm}"
    )
