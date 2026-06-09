"""Тесты личного кабинета, метрик и базы знаний."""

from datetime import date, timedelta

from dirizher.container import AppContainer
from dirizher.domain.enums import TaskSource, TaskStatus
from dirizher.domain.models import SourceRef, TeamMember

TODAY = date(2026, 6, 7)


async def test_cabinet_tracks_speed_quality_notes_and_achievements():
    c = AppContainer()
    member = c.team.register(
        TeamMember(user_id=1, username="maxim", full_name="Максим", dm_chat_id=1001)
    )
    processed = await c.service.ingest(
        "Максим, сделай авторизацию к пятнице",
        SourceRef(source=TaskSource.chat, chat_id=10),
        today=TODAY,
    )
    task = await c.service.create_on_board(processed[0].task)
    task.deadline = TODAY + timedelta(days=1)
    c.cabinet.add_note(member, "проверить OAuth")
    c.cabinet.record_report(member)

    await c.service.set_status(task, TaskStatus.in_progress)
    await c.service.set_status(task, TaskStatus.done)

    stats = c.cabinet.stats_for(member, today=TODAY)
    assert stats.done == 1
    assert stats.open == 0
    assert stats.on_time_done == 1
    assert stats.xp > 0
    assert "Первый закрытый таск" in " ".join(stats.achievements)
    assert "OAuth" in c.cabinet.render_notes(member)
    assert "Личный кабинет" in c.cabinet.render_profile(member)


async def test_knowledge_base_adds_and_searches_items():
    c = AppContainer()
    item = c.cabinet.add_knowledge("Демо", "Сценарий демо показываем через Telegram", "maxim")
    found = c.cabinet.search_knowledge("telegram демо")

    assert found == [item]
    rendered = c.cabinet.render_knowledge(found)
    assert "База знаний" in rendered
    assert "Сценарий демо" in rendered


async def test_private_report_can_be_bound_to_source_group_chat():
    from types import SimpleNamespace

    from dirizher.bot.handlers.commands import _report_chat_id

    c = AppContainer()
    member = c.team.register(
        TeamMember(user_id=1, username="maxim", full_name="Максим", dm_chat_id=999)
    )
    processed = await c.service.ingest(
        "Максим, сделай авторизацию к пятнице",
        SourceRef(source=TaskSource.chat, chat_id=123),
        today=TODAY,
    )
    await c.service.create_on_board(processed[0].task)

    private_message = SimpleNamespace(chat=SimpleNamespace(id=999, type="private"))
    chat_id = _report_chat_id(private_message, c, member)
    assert chat_id == 123

    await c.reconciliation.record_report(chat_id, "maxim", "авторизацию сделал", today=TODAY)
    _digest, silent = c.reconciliation.evening_digest(123, today=TODAY)
    assert "@maxim" not in silent


async def test_notification_falls_back_to_group_chat():
    from dirizher.bot.notifications import send_with_fallback

    class FakeBot:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text):
            if chat_id == 1:
                raise RuntimeError("DM blocked")
            self.sent.append((chat_id, text))

    bot = FakeBot()
    ok = await send_with_fallback(bot, 1, "hello", fallback_chat_id=2)

    assert ok is True
    assert bot.sent == [(2, "hello")]


def test_personal_notes_can_be_edited_deleted_and_cleared():
    c = AppContainer()
    member = c.team.register(TeamMember(user_id=1, username="maxim", full_name="Максим"))
    first = c.cabinet.add_note(member, "старый текст")
    c.cabinet.add_note(member, "вторая заметка")

    updated = c.cabinet.edit_note(member, first.id, "новый текст")
    assert updated is not None
    assert updated.text == "новый текст"
    assert "новый текст" in c.cabinet.render_notes(member)

    removed = c.cabinet.delete_note(member, first.id)
    assert removed is not None
    assert removed.text == "новый текст"
    assert all(note.id != first.id for note in c.cabinet.notes_for(member, limit=None))

    assert c.cabinet.clear_notes(member) == 1
    assert c.cabinet.notes_for(member) == []


def test_knowledge_items_can_be_edited_deleted_and_cleared():
    c = AppContainer()
    first = c.cabinet.add_knowledge("Демо", "Старый сценарий", "maxim")
    c.cabinet.add_knowledge("Регламент", "Созвон в 10", "dasha")

    updated = c.cabinet.edit_knowledge(first.id, "Новый демо", "Новый сценарий", "dasha")
    assert updated is not None
    assert updated.title == "Новый демо"
    assert updated.text == "Новый сценарий"
    assert c.cabinet.search_knowledge("старый") == []
    assert c.cabinet.search_knowledge("новый") == [updated]

    removed = c.cabinet.delete_knowledge(first.id)
    assert removed is not None
    assert removed.title == "Новый демо"
    assert c.cabinet.get_knowledge(first.id) is None

    assert c.cabinet.clear_knowledge() == 1
    assert c.cabinet.recent_knowledge() == []
