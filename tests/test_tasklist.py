"""Тесты интерактивного списка задач и распознавания вопроса «мои задачи»."""

import pytest

from dirizher.bot.handlers.commands import _card_belongs_to
from dirizher.bot.handlers.messages import _is_my_tasks_query
from dirizher.bot.keyboards import board_task_keyboard
from dirizher.domain.enums import TaskStatus
from dirizher.domain.models import TeamMember
from dirizher.integrations.yougile import BoardCard


@pytest.mark.parametrize("text", [
    "какие у меня задачи?",
    "Какие у меня таски?",
    "мои задачи",
    "покажи мои таски",
    "что у меня по задачам",
    "список задач",
])
def test_my_tasks_query_detected(text):
    assert _is_my_tasks_query(text) is True


@pytest.mark.parametrize("text", [
    "Данила сделай бота к среде",
    "поставь задачу на завтра",
    "привет",
    "закрой задачу по презентации",
])
def test_my_tasks_query_not_triggered(text):
    assert _is_my_tasks_query(text) is False


def test_card_belongs_to_by_alias():
    m = TeamMember(user_id=1, username="danya", full_name="Данила Скиба", aliases=["Дэн"])
    assert _card_belongs_to(BoardCard(id="x", title="t", assignee="Данила Скиба"), m) is True
    assert _card_belongs_to(BoardCard(id="x", title="t", assignee="Дэн"), m) is True
    assert _card_belongs_to(BoardCard(id="x", title="t", assignee="Андрей"), m) is False
    assert _card_belongs_to(BoardCard(id="x", title="t", assignee=None), m) is False


def test_board_keyboard_checkmark_on_current_status():
    kb = board_task_keyboard("card1", TaskStatus.in_progress)
    labels = [b.text for row in kb.inline_keyboard for b in row]
    # галочка только у текущего статуса
    assert any(label.startswith("✓ ") and "В работу" in label for label in labels)
    assert not any(label.startswith("✓ ") and "Готово" in label for label in labels)
    assert any("Удалить" in label for label in labels)


def test_board_keyboard_delete_confirmation():
    kb = board_task_keyboard("card1", TaskStatus.todo, confirm_delete=True)
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert any("Да, удалить" in label for label in labels)
    assert any("Отмена" in label for label in labels)


def test_board_keyboard_delete_confirmation_preserves_current_status():
    kb = board_task_keyboard("card1", TaskStatus.in_progress, confirm_delete=True)
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert any(label.startswith("✓ ") and "В работу" in label for label in labels)
    assert any("Да, удалить" in label for label in labels)
    assert any("Отмена" in label for label in labels)


def test_render_board_has_single_section_per_status():
    from dirizher.bot.text import render_board

    text = render_board([
        BoardCard(id="todo-1", title="Сделать бота", status=TaskStatus.todo, assignee="Данила"),
        BoardCard(id="done-1", title="Проверить UI", status=TaskStatus.done),
    ])

    assert text.count("<b>К выполнению</b>") == 1
    assert text.count("<b>В работе</b>") == 1
    assert text.count("<b>Готово</b>") == 1
    assert "<b>Статус:</b> К выполнению" in text
    assert "<b>Исполнитель:</b> Данила" in text
    assert "<b>Дедлайн:</b> без срока" in text


def test_render_board_marks_past_deadline_as_overdue():
    from datetime import date, timedelta

    from dirizher.bot.text import render_board

    text = render_board([
        BoardCard(
            id="late-1",
            title="Просроченная задача",
            status=TaskStatus.todo,
            deadline=date.today() - timedelta(days=1),
        )
    ])

    assert "<b>Просроченные</b> · 1" in text
    assert "<b>Статус:</b> Просроченные" in text


def test_board_keyboard_has_no_manual_overdue_button():
    kb = board_task_keyboard("card1", TaskStatus.todo)
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert not any("Просроч" in label for label in labels)


def test_tasks_command_target_can_be_plain_username():
    from dirizher.bot.handlers.commands import _resolve_target
    from dirizher.container import AppContainer

    c = AppContainer()
    member = c.team.register(TeamMember(user_id=42, username="alice", full_name="Alice"))

    target, label, is_self = _resolve_target(c, "alice", author=None)

    assert target is member
    assert label == "@alice"
    assert is_self is False


def test_main_help_is_split_into_sections():
    from dirizher.bot.handlers import commands

    assert "/help_meetings" in commands.HELP
    assert "/help_profile" in commands.HELP
    assert "/help_kb" in commands.HELP
    assert "/help_admin" in commands.HELP
    assert "/enroll_voice" not in commands.HELP
    assert "/enroll_voice" in commands.HELP_MEETINGS
    assert "/kb_add" in commands.HELP_KB
    assert "/team_create" in commands.HELP_ADMIN
