"""Колбэки кнопок: подтверждение, правка, дубли, уточнение, статусы задач."""

from __future__ import annotations

from html import escape as esc

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ...container import AppContainer
from ...domain.enums import TaskStatus
from ...domain.models import TeamMember
from ...permissions import can_change_task_status, can_create_task, can_delete_task, can_manage_task, is_superuser
from ...logging_setup import get_logger
from .. import keyboards as kb
from .. import text as tx
from ..callback_data import BoardCD, ConfirmCD, ForgetCD, PickCD, TaskCD
from ..flow import finalize, notify_workload
from ..states import EditTask

router = Router(name="callbacks")
log = get_logger("dirizher.bot.callbacks")


def _actor(cb: CallbackQuery, c: AppContainer) -> TeamMember | None:
    user = cb.from_user
    if user is None:
        return None
    return c.team.register(TeamMember(user_id=user.id, username=user.username, full_name=user.full_name))


def _ignore_unauthorized(cb: CallbackQuery) -> object:
    return cb.answer()


async def _finish(cb: CallbackQuery, text: str) -> None:
    if isinstance(cb.message, Message):
        await cb.message.edit_text(text)
    await cb.answer()


async def _celebrate(c: AppContainer, message, task) -> None:
    """Начислить XP за закрытие и прислать короткое поздравление (если есть)."""
    try:
        lines = c.game.complete(task)
    except Exception as e:  # noqa: BLE001
        log.warning("Геймификация: не удалось начислить XP: %s", e)
        return
    if lines and isinstance(message, Message):
        await message.answer("\n".join(lines))


async def _after_created(cb: CallbackQuery, c: AppContainer, created) -> None:
    if isinstance(cb.message, Message):
        await notify_workload(cb.bot, c, created.assignee, cb.message.chat.id)


@router.callback_query(ConfirmCD.filter())
async def on_confirm(cb: CallbackQuery, callback_data: ConfirmCD, c: AppContainer, state: FSMContext) -> None:
    action, pid = callback_data.action, callback_data.pid
    pending = c.pending.get(pid)
    if pending is None:
        await cb.answer("Карточка устарела 🙈", show_alert=False)
        return
    actor = _actor(cb, c)
    if action in {"confirm", "edit", "reject", "dup_merge", "dup_new", "clarify_yes", "clarify_no"} and not can_create_task(actor, pending.task, c.team):
        await cb.answer()
        return

    if action == "confirm":
        c.pending.pop(pid)
        created = await c.service.create_on_board(pending.task)
        await _finish(cb, tx.render_created(created))
        await _after_created(cb, c, created)

    elif action == "reject":
        c.pending.pop(pid)
        await _finish(cb, f"❌ Отклонено: «{esc(pending.task.title)}»")

    elif action == "edit":
        await state.set_state(EditTask.waiting_correction)
        await state.update_data(pid=pid)
        await cb.message.answer(
            "✏️ Что поправить? Напишите текстом или пришлите голосовое.\n"
            "Например: «перенеси на пятницу», «назначь на Дашу», «сделай срочной»."
        )
        await cb.answer()

    elif action == "dup_merge":
        c.pending.pop(pid)
        existing = c.repo.get(pending.duplicate_of_id or "")
        if existing:
            merged = await c.service.merge_duplicate(existing, pending.source)
            await _finish(cb, f"🔗 Объединил с «{esc(merged.title)}». Источников: {len(merged.sources)}.")
        else:
            created = await c.service.create_on_board(pending.task)
            await _finish(cb, tx.render_created(created))
            await _after_created(cb, c, created)

    elif action == "dup_new":
        c.pending.pop(pid)
        created = await c.service.create_on_board(pending.task)
        await _finish(cb, tx.render_created(created))
        await _after_created(cb, c, created)

    elif action == "clarify_yes":
        c.pending.pop(pid)
        created = await c.service.create_on_board(pending.task)
        await _finish(cb, tx.render_created(created))
        await _after_created(cb, c, created)

    elif action == "clarify_no":
        c.pending.pop(pid)
        await _finish(cb, "🚫 Понял, не завожу.")


# ── Выбор исполнителя при коллизии имён (#1) ─────────────────────────────────
@router.callback_query(PickCD.filter())
async def on_pick_assignee(cb: CallbackQuery, callback_data: PickCD, c: AppContainer) -> None:
    pending = c.pending.get(callback_data.pid)
    if pending is None:
        await cb.answer("Карточка устарела 🙈", show_alert=False)
        return

    actor = _actor(cb, c)
    if not can_create_task(actor, pending.task, c.team):
        await cb.answer()
        return

    if callback_data.action == "cancel":
        c.pending.pop(callback_data.pid)
        await _finish(cb, f"🚫 Не завожу: «{esc(pending.task.title)}» (исполнитель не выбран).")
        return

    options = pending.assignee_options
    try:
        chosen = options[int(callback_data.idx)]
    except (ValueError, IndexError):
        await cb.answer("Не понял выбор 🙈", show_alert=False)
        return

    c.pending.pop(callback_data.pid)
    # Закрепляем конкретного человека за задачей.
    pending.task.assignee = chosen.username or chosen.full_name
    pending.task.assignee_yougile_ids = [chosen.yougile_id] if chosen.yougile_id else []

    who = chosen.full_name or chosen.username or "участник"
    if isinstance(cb.message, Message):
        await cb.message.edit_text(f"👤 Исполнитель: <b>{esc(who)}</b>\n\n" + tx.render_task_card(pending.task))
    await cb.answer(f"Назначено: {who}")

    # Дальше — обычный поток (авто-создание или подтверждение).
    from ...services.task_service import ProcessedTask

    dup = c.repo.get(pending.duplicate_of_id) if pending.duplicate_of_id else None
    p = ProcessedTask(task=pending.task, outcome=pending.outcome, duplicate_of=dup)
    await finalize(cb.bot, c, p, pending.chat_id)


# ── Приём правки (FSM) ───────────────────────────────────────────────────────
@router.message(EditTask.waiting_correction, F.text)
async def on_correction(message: Message, c: AppContainer, state: FSMContext) -> None:
    data = await state.get_data()
    pid = data.get("pid")
    await state.clear()
    pending = c.pending.get(pid or "")
    if pending is None:
        await message.answer("Карточка для правки не найдена 🙈")
        return
    actor = c.team.register(TeamMember(user_id=message.from_user.id, username=message.from_user.username, full_name=message.from_user.full_name)) if message.from_user else None
    if not can_manage_task(actor, pending.task, c.team):
        return
    await c.service.apply_correction(pending.task, message.text or "")
    await message.answer(
        "Переформулировал:\n\n" + tx.render_task_card(pending.task, header="✏️ Поправленная задача"),
        reply_markup=kb.confirm_keyboard(pending.pid),
    )


# ── Управление статусом задачи ───────────────────────────────────────────────
@router.callback_query(TaskCD.filter())
async def on_task_action(cb: CallbackQuery, callback_data: TaskCD, c: AppContainer) -> None:
    task = c.repo.get(callback_data.task_id)
    if task is None:
        await cb.answer("Задача не найдена", show_alert=False)
        return
    actor = _actor(cb, c)
    if not can_change_task_status(actor, task, c.team):
        await cb.answer()
        return
    if callback_data.action == "done":
        await c.service.set_status(task, TaskStatus.done)
        await cb.answer("✅ Готово")
        await _celebrate(c, cb.message, task)
    elif callback_data.action == "start":
        await c.service.set_status(task, TaskStatus.in_progress)
        await cb.answer("▶️ В работе")
    if isinstance(cb.message, Message):
        await cb.message.edit_reply_markup(reply_markup=kb.task_actions_keyboard(task.id))


# ── Очистка памяти о команде ─────────────────────────────────────────────────
@router.callback_query(ForgetCD.filter())
async def on_forget(cb: CallbackQuery, callback_data: ForgetCD, c: AppContainer) -> None:
    if not is_superuser(_actor(cb, c)):
        await cb.answer()
        return
    if callback_data.action == "no":
        await _finish(cb, "↩️ Отменено — участники на месте.")
        return
    n = c.team.clear()
    c.persist()
    await _finish(
        cb,
        f"🗑️ Забыл всех участников ({n}). Команда соберётся заново: пусть каждый "
        f"нажмёт /start → «Представиться» и укажет email с доски.",
    )


# ── Управление карточкой доски («мои задачи») ────────────────────────────────
_BOARD_STATUS = {
    "todo": TaskStatus.todo,
    "in_progress": TaskStatus.in_progress,
    "done": TaskStatus.done,
}


async def _card_status(c: AppContainer, card_id: str, fallback: TaskStatus = TaskStatus.todo) -> TaskStatus:
    """Текущий статус карточки из локальной памяти или живой доски."""
    task = c.repo.get_by_card(card_id)
    if task is not None:
        return task.status
    try:
        for card in await c.board.list_cards():
            if card.id == card_id:
                return card.status
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось получить статус карточки %s: %s", card_id, e)
    return fallback


@router.callback_query(BoardCD.filter())
async def on_board_action(cb: CallbackQuery, callback_data: BoardCD, c: AppContainer) -> None:
    action, cid = callback_data.action, callback_data.cid
    msg = cb.message if isinstance(cb.message, Message) else None
    actor = _actor(cb, c)
    task_for_acl = c.repo.get_by_card(cid)
    if c.team.superuser_exists() and task_for_acl is None and not is_superuser(actor):
        await cb.answer()
        return
    if task_for_acl is not None and task_for_acl.trashed_at is not None:
        await cb.answer("Задача в корзине. Восстановите через /task_restore", show_alert=False)
        return

    # Смена статуса (todo / in_progress / done). Просрочка выставляется автоматически по дедлайну.
    if action in _BOARD_STATUS:
        status = _BOARD_STATUS[action]
        task = task_for_acl
        if task is not None and not can_change_task_status(actor, task, c.team):
            await cb.answer()
            return
        try:
            if task is not None:
                await c.service.set_status(task, status)
            else:
                # В /tasks карточка может прийти прямо из YouGile и отсутствовать в памяти.
                await c.board.move_card(cid, status)
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось сменить статус карточки %s: %s", cid, e)
            await cb.answer("Не получилось обновить на доске 🙈", show_alert=True)
            return
        await cb.answer(f"{status.label_ru} ✓")
        if status is TaskStatus.done and task is not None:
            await _celebrate(c, msg, task)
        if msg:
            await msg.edit_reply_markup(reply_markup=kb.board_task_keyboard(cid, status, allow_delete=task is not None and can_delete_task(actor, task, c.team)))
        return

    # Запрос на удаление → показать подтверждение
    if action == "del":
        if task_for_acl is None:
            await cb.answer("Не могу удалить без корзины: сначала синхронизируйте задачу", show_alert=True)
            return
        if not can_delete_task(actor, task_for_acl, c.team):
            await cb.answer()
            return
        current = await _card_status(c, cid)
        if msg:
            await msg.edit_reply_markup(
                reply_markup=kb.board_task_keyboard(cid, current, confirm_delete=True, allow_delete=task_for_acl is not None and can_delete_task(actor, task_for_acl, c.team))
            )
        await cb.answer("Удалить задачу?")
        return

    if action == "del_no":
        current = await _card_status(c, cid)
        if msg:
            await msg.edit_reply_markup(reply_markup=kb.board_task_keyboard(cid, current, allow_delete=task_for_acl is not None and can_delete_task(actor, task_for_acl, c.team)))
        await cb.answer("Отменено")
        return

    if action == "del_yes":
        task = task_for_acl
        if task is None:
            await cb.answer("Не могу удалить без корзины: сначала синхронизируйте задачу", show_alert=True)
            return
        if not can_delete_task(actor, task, c.team):
            await cb.answer()
            return
        try:
            await c.service.soft_delete_task(task)
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось удалить карточку %s: %s", cid, e)
            await cb.answer("Не получилось удалить 🙈", show_alert=True)
            return
        await cb.answer("🗑️ В корзине на 4 часа")
        if msg:
            await msg.edit_text("🗑️ Задача перемещена в корзину на 4 часа. Её можно восстановить через /trash.")
        return
