"""Inline-клавиатуры сценария подтверждения и управления задачами."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..domain.enums import TaskStatus
from ..domain.models import TeamMember
from .callback_data import BoardCD, ConfirmCD, ForgetCD, IntroCD, PickCD, SettingsCD, TaskCD


def _button(text: str, callback_data) -> InlineKeyboardButton:
    packed = callback_data.pack() if hasattr(callback_data, "pack") else callback_data
    return InlineKeyboardButton(text=text, callback_data=packed)


def confirm_keyboard(pid: str, *, claim_name: str | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Подтвердить", callback_data=ConfirmCD(action="confirm", pid=pid))
    kb.button(text="✏️ Поправить", callback_data=ConfirmCD(action="edit", pid=pid))
    kb.button(text="❌ Отклонить", callback_data=ConfirmCD(action="reject", pid=pid))
    if claim_name:
        # Исполнитель неизвестен боту — даём кнопку «это я», чтобы человек закрепился
        short = claim_name[:20]
        kb.button(
            text=f"👋 Это я ({short})",
            callback_data=IntroCD(action="claim", pid=pid, name=short),
        )
        kb.adjust(2, 1, 1)
    else:
        kb.adjust(2, 1)
    return kb.as_markup()


def pick_assignee_keyboard(pid: str, options: list[TeamMember]) -> InlineKeyboardMarkup:
    """Кнопки выбора конкретного тёзки, когда имя исполнителя неоднозначно (#1)."""
    kb = InlineKeyboardBuilder()
    for idx, m in enumerate(options[:6]):
        name = m.full_name or m.username or "участник"
        label = f"{name} (@{m.username})" if m.username and m.username not in name else name
        kb.button(text=f"👤 {label[:40]}", callback_data=PickCD(action="pick", pid=pid, idx=str(idx)))
    kb.button(text="❌ Отмена", callback_data=PickCD(action="cancel", pid=pid))
    kb.adjust(1)
    return kb.as_markup()


def introduce_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="👋 Представиться", callback_data=IntroCD(action="self"))
    return kb.as_markup()


def forget_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑️ Да, забыть всех", callback_data=ForgetCD(action="yes"))
    kb.button(text="↩️ Отмена", callback_data=ForgetCD(action="no"))
    kb.adjust(1)
    return kb.as_markup()


def duplicate_keyboard(pid: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔗 Та же — объединить", callback_data=ConfirmCD(action="dup_merge", pid=pid))
    kb.button(text="➕ Создать новую", callback_data=ConfirmCD(action="dup_new", pid=pid))
    kb.adjust(1)
    return kb.as_markup()


def clarify_keyboard(pid: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, завести", callback_data=ConfirmCD(action="clarify_yes", pid=pid))
    kb.button(text="🚫 Нет", callback_data=ConfirmCD(action="clarify_no", pid=pid))
    kb.adjust(2)
    return kb.as_markup()


def task_actions_keyboard(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            _button("▶️ В работу", TaskCD(action="start", task_id=task_id)),
            _button("✅ Готово", TaskCD(action="done", task_id=task_id)),
        ]]
    )


# ── Управление карточкой доски (мои задачи) ──────────────────────────────────
# action для статусов совпадает с TaskStatus.value, чтобы ставить «галочку».
# «Просрочено» — вычисляемый статус (дедлайн прошёл), руками его не ставят,
# поэтому кнопки для него нет.
_STATUS_BTN = {
    TaskStatus.todo: ("📋 К выполнению", "todo"),
    TaskStatus.in_progress: ("▶️ В работу", "in_progress"),
    TaskStatus.done: ("✅ Готово", "done"),
}


def board_task_keyboard(
    card_id: str,
    current: TaskStatus,
    *,
    confirm_delete: bool = False,
    allow_delete: bool = True,
    allow_status: bool = True,
) -> InlineKeyboardMarkup:
    """Клавиатура управления задачей: 3 статуса (с ✓ у текущего) + удаление.

    При confirm_delete показываем подтверждение удаления вместо обычного ряда.
    """
    status_row = [
        _button(
            f"{'✓ ' if status == current else ''}{label}",
            BoardCD(action=action, cid=card_id),
        )
        for status, (label, action) in _STATUS_BTN.items()
    ]

    rows = [status_row] if allow_status else []
    if not allow_delete:
        return InlineKeyboardMarkup(inline_keyboard=rows)
    action_row = (
        [
            _button("🗑️ Да, удалить", BoardCD(action="del_yes", cid=card_id)),
            _button("↩️ Отмена", BoardCD(action="del_no", cid=card_id)),
        ]
        if confirm_delete
        else [_button("🗑️ Удалить", BoardCD(action="del", cid=card_id))]
    )
    rows.append(action_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_keyboard(member: TeamMember) -> InlineKeyboardMarkup:
    """Клавиатура личных настроек уведомлений."""
    enabled = bool(member.notify_gamification)
    kb = InlineKeyboardBuilder()
    kb.button(
        text=("✅ Геймификация включена" if enabled else "🔕 Геймификация выключена"),
        callback_data=SettingsCD(action="gamification_off" if enabled else "gamification_on"),
    )
    kb.button(text="✖️ Закрыть", callback_data=SettingsCD(action="close"))
    kb.adjust(1)
    return kb.as_markup()
