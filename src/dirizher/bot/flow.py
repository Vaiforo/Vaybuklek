"""Общий поток предъявления извлечённых задач (используется текстом и голосом).

Решает, что делать с каждой обработанной задачей в зависимости от режима чата:
- авто-режим (True): новые задачи и объединения применяются сразу;
- ручной режим (False): показываем карточку и кнопки подтверждения/правки.

Важно: порог уверенности — отдельный страж от фантомных задач: даже в
авто-режиме задачи с низкой уверенностью выносятся на уточнение.
"""

from __future__ import annotations

from html import escape as esc

from aiogram import Bot

from ..container import AppContainer
from ..services.task_service import Outcome, ProcessedTask
from . import keyboards as kb
from . import text as tx
from .notifications import send_with_fallback


async def deliver_xp(
    bot: Bot,
    c: AppContainer,
    lines: list[str],
    *,
    assignee: str | None = None,
    dm_chat_id: int | None = None,
    chat_id: int,
) -> None:
    """Доставить строки начисления XP/ачивок.

    По умолчанию (game_announce_in_chat=False) — только в личку исполнителю,
    чтобы не спамить общий чат; если лички нет — молчим. С флагом — в общий чат.
    """
    if not lines:
        return
    text = "\n".join(lines)
    if c.settings.schedule.game_announce_in_chat:
        await bot.send_message(chat_id, text)
        return
    member = c.team.resolve(assignee) if assignee is not None else None
    if member is None or not member.notify_gamification:
        return
    if dm_chat_id is None:
        dm_chat_id = member.dm_chat_id
    if dm_chat_id:
        await send_with_fallback(bot, dm_chat_id, text)  # без fallback в чат → тихо


async def notify_workload(bot: Bot, c: AppContainer, assignee: str | None, chat_id: int) -> None:
    """Предупредить о перегрузке: лично, если пользователь открыл диалог, иначе в чат."""
    warning = c.service.workload_warning(assignee)
    if not warning:
        return
    member = c.team.resolve(assignee)
    target = member.dm_chat_id if member and member.dm_chat_id else chat_id
    await send_with_fallback(bot, target, warning, fallback_chat_id=chat_id)


async def notify_assignee(bot: Bot, c: AppContainer, created, chat_id: int) -> None:
    """Отправить персональное уведомление исполнителю, если доступен личный чат."""
    member = c.team.resolve(created.assignee)
    if not member or not member.dm_chat_id or member.dm_chat_id == chat_id:
        return
    await send_with_fallback(
        bot,
        member.dm_chat_id,
        "🎯 <b>Вам назначена задача</b>\n\n"
        + tx.render_task_card(created, header="📌 Назначение")
        + "\n\n🎮 XP начислю, когда задача перейдёт в «Готово».",
        fallback_chat_id=chat_id,
    )


async def present(bot: Bot, c: AppContainer, processed: list[ProcessedTask], chat_id: int) -> None:
    if not processed:
        return
    for p in processed:
        # Коллизия имён: имя исполнителя подходит нескольким — сперва уточняем,
        # кто именно берётся за задачу (#1), и только потом заводим/подтверждаем.
        if p.ambiguous:
            await _ask_pick_assignee(bot, c, p, chat_id)
        else:
            await finalize(bot, c, p, chat_id)


async def finalize(bot: Bot, c: AppContainer, p: ProcessedTask, chat_id: int) -> None:
    """Завести/подтвердить/уточнить одну задачу (исполнитель уже однозначен)."""
    auto = c.mode.is_auto(chat_id)
    if p.outcome is Outcome.low_confidence:
        await _ask_clarify(bot, c, p, chat_id)
    elif p.outcome is Outcome.duplicate:
        if auto and p.duplicate_of:
            merged = await c.service.merge_duplicate(p.duplicate_of, p.task.sources[0])
            await bot.send_message(
                chat_id,
                f"♻️ Объединил с существующей: «{esc(merged.title)}» (источники: "
                f"{len(merged.sources)}).",
            )
        else:
            await _ask_duplicate(bot, c, p, chat_id)
    else:  # new
        if auto:
            created = await c.service.create_on_board(p.task)
            await bot.send_message(chat_id, tx.render_created(created))
            await notify_assignee(bot, c, created, chat_id)
            await notify_workload(bot, c, created.assignee, chat_id)
        else:
            await _ask_confirm(bot, c, p, chat_id)


def _unknown_assignee(c: AppContainer, p: ProcessedTask) -> str | None:
    """Первый исполнитель, которого бот не знает (нельзя тегать) — иначе None.

    Поддерживает несколько исполнителей через запятую: ищем первого незнакомого.
    """
    from ..services.task_service import _split_assignees

    for name in _split_assignees(p.task.assignee):
        if c.team.resolve(name) is None:
            return name
    return None


async def _ask_confirm(bot: Bot, c: AppContainer, p: ProcessedTask, chat_id: int) -> None:
    pending = c.pending.put(p, chat_id)
    unknown = _unknown_assignee(c, p)
    body = tx.render_processed(p)
    if unknown:
        body += (
            f"\n\n⚠️ Я пока не знаю, кто такой «{esc(unknown)}». "
            f"Пусть он нажмёт «👋 Это я», или поправьте исполнителя."
        )
    await bot.send_message(
        chat_id,
        body,
        reply_markup=kb.confirm_keyboard(pending.pid, claim_name=unknown),
    )


async def _ask_duplicate(bot: Bot, c: AppContainer, p: ProcessedTask, chat_id: int) -> None:
    pending = c.pending.put(p, chat_id)
    await bot.send_message(
        chat_id,
        tx.render_processed(p),
        reply_markup=kb.duplicate_keyboard(pending.pid),
    )


async def _ask_clarify(bot: Bot, c: AppContainer, p: ProcessedTask, chat_id: int) -> None:
    pending = c.pending.put(p, chat_id)
    await bot.send_message(
        chat_id,
        tx.render_processed(p),
        reply_markup=kb.clarify_keyboard(pending.pid),
    )


async def _ask_pick_assignee(bot: Bot, c: AppContainer, p: ProcessedTask, chat_id: int) -> None:
    """Коллизия имён: имя исполнителя носит несколько участников — уточняем, кто берётся."""
    pending = c.pending.put(p, chat_id)
    name = esc(p.task.assignee or "")
    body = (
        tx.render_processed(p)
        + f"\n\n🙋 Имя «{name}» носят несколько участников. "
        f"Кто именно берётся за задачу?"
    )
    await bot.send_message(
        chat_id,
        body,
        reply_markup=kb.pick_assignee_keyboard(pending.pid, pending.assignee_options),
    )
