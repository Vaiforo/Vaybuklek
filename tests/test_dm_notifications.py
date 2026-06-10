"""Личные уведомления исполнителям о подтверждённых/изменённых задачах в ЛС.

Проверяем чистую логику рассылки (flow._dm_assignees через notify_assignee /
notify_assignee_edited) с фейковым ботом — без Telegram.
"""

from __future__ import annotations

from dirizher.bot.flow import notify_assignee, notify_assignee_edited
from dirizher.container import AppContainer
from dirizher.domain.models import Task, TeamMember


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **_kw):
        self.sent.append((chat_id, text))


def _container() -> AppContainer:
    c = AppContainer()
    # У двух исполнителей открыта личка, у третьего — нет.
    c.team.register(TeamMember(user_id=1, username="anna", full_name="Анна", dm_chat_id=1001))
    c.team.register(TeamMember(user_id=2, username="max", full_name="Максим", dm_chat_id=1002))
    c.team.register(TeamMember(user_id=3, username="petya", full_name="Петя"))  # нет dm
    return c


GROUP = -500


async def test_dm_sent_to_assignee_with_private_chat():
    c = _container()
    bot = FakeBot()
    task = Task(title="Сделать отчёт", assignee="anna")
    await notify_assignee(bot, c, task, GROUP)
    assert len(bot.sent) == 1
    chat_id, text = bot.sent[0]
    assert chat_id == 1001
    assert "назначена задача" in text.lower()
    assert "Сделать отчёт" in text


async def test_dm_sent_to_each_of_multiple_assignees():
    c = _container()
    bot = FakeBot()
    task = Task(title="Запустить демо", assignee="anna, max")
    await notify_assignee(bot, c, task, GROUP)
    assert {cid for cid, _ in bot.sent} == {1001, 1002}


async def test_no_dm_when_assignee_has_no_private_chat():
    c = _container()
    bot = FakeBot()
    task = Task(title="Что-то", assignee="petya")
    await notify_assignee(bot, c, task, GROUP)
    assert bot.sent == []  # нет лички — в группу не дублируем


async def test_opt_out_disables_dm():
    c = _container()
    c.team.resolve("anna").notify_assignment = False
    bot = FakeBot()
    task = Task(title="Отчёт", assignee="anna, max")
    await notify_assignee(bot, c, task, GROUP)
    # Анна отписалась — придёт только Максиму.
    assert {cid for cid, _ in bot.sent} == {1002}


async def test_no_dm_when_confirmed_in_own_private_chat():
    c = _container()
    bot = FakeBot()
    task = Task(title="Личная", assignee="anna")
    # Подтверждение шло в личке самой Анны (chat_id == её dm) — не дублируем.
    await notify_assignee(bot, c, task, 1001)
    assert bot.sent == []


async def test_edit_notification_uses_changed_header():
    c = _container()
    bot = FakeBot()
    task = Task(title="Поправленная", assignee="max")
    await notify_assignee_edited(bot, c, task, GROUP)
    assert len(bot.sent) == 1
    assert "измени" in bot.sent[0][1].lower()
