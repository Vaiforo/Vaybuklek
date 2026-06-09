"""Тесты разрешения исполнителя: коллизия тёзок (#1) и fallback на автора (#2)."""

from datetime import date

import pytest

from dirizher.container import AppContainer
from dirizher.domain.enums import TaskSource
from dirizher.domain.models import SourceRef, TeamMember

TODAY = date(2026, 6, 7)  # суббота


@pytest.fixture
def c():
    cont = AppContainer()
    # Два тёзки «Саша» — коллизия имён.
    cont.team.register(TeamMember(user_id=1, username="sasha_a", full_name="Саша Иванов", aliases=["Саша"]))
    cont.team.register(TeamMember(user_id=2, username="sasha_b", full_name="Саша Петров", aliases=["Саша"]))
    # Уникальный участник — автор-отправитель для fallback.
    cont.team.register(TeamMember(user_id=3, username="danya", full_name="Данила", aliases=["Даня"]))
    return cont


def _src():
    return SourceRef(source=TaskSource.chat, chat_id=1)


# ── #1: коллизия имён → задача помечается ambiguous со списком кандидатов ─────
async def test_namesake_collision_marks_ambiguous(c):
    processed = await c.service.ingest("Саша, сделай отчёт к среде", _src(), today=TODAY)
    assert len(processed) == 1
    p = processed[0]
    # Имя не залочено на первого тёзку — ждём уточнения «кто именно».
    assert p.task.assignee == "Саша"
    assert {m.user_id for m in p.ambiguous} == {1, 2}


async def test_unique_name_not_ambiguous(c):
    processed = await c.service.ingest("Данила, сделай отчёт к среде", _src(), today=TODAY)
    p = processed[0]
    assert p.ambiguous == []
    assert p.task.assignee == "danya"  # нормализован к username


# ── #2: исполнитель не указан → берём автора сообщения ───────────────────────
async def test_assignee_falls_back_to_sender(c):
    sender = c.team.get_by_user_id(3)
    processed = await c.service.ingest(
        "Нужно сделать отчёт к среде", _src(), today=TODAY, sender=sender
    )
    p = processed[0]
    assert p.task.assignee == "danya"
    assert p.ambiguous == []


async def test_no_sender_keeps_assignee_none(c):
    # Без отправителя (встреча/симуляция) исполнитель остаётся пустым.
    processed = await c.service.ingest("Нужно сделать отчёт к среде", _src(), today=TODAY)
    p = processed[0]
    assert p.task.assignee is None
