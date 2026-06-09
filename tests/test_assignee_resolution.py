"""Тесты разрешения исполнителя: коллизия тёзок (#1) и fallback на автора (#2)."""

from datetime import date

import pytest

from dirizher.container import AppContainer
from dirizher.domain.enums import TaskSource
from dirizher.domain.models import SourceRef, Team, TeamMember

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


# ── #2: исполнитель не указан → задача остаётся обезличенной ─────────────────
async def test_missing_assignee_stays_unassigned_even_with_sender(c):
    sender = c.team.get_by_user_id(3)
    processed = await c.service.ingest(
        "Нужно сделать отчёт к среде", _src(), today=TODAY, sender=sender
    )
    p = processed[0]
    assert p.task.assignee is None
    assert p.task.team_id is None
    assert p.ambiguous == []


async def test_no_sender_keeps_assignee_none(c):
    # Без отправителя (встреча/симуляция) исполнитель остаётся пустым.
    processed = await c.service.ingest("Нужно сделать отчёт к среде", _src(), today=TODAY)
    p = processed[0]
    assert p.task.assignee is None


async def test_multiteam_assignee_uses_sender_leader_team():
    cont = AppContainer()
    team_a = cont.team.add_team(Team(name="A"))
    team_b = cont.team.add_team(Team(name="B"))
    worker = cont.team.register(TeamMember(user_id=10, username="worker", full_name="Worker", aliases=["Вася"]))
    leader = cont.team.register(TeamMember(user_id=11, username="lead", full_name="Lead"))
    cont.team.assign_member_to_team(worker, team_a)
    cont.team.assign_member_to_team(worker, team_b)
    cont.team.assign_member_to_team(leader, team_b, leader=True)

    processed = await cont.service.ingest("Вася, сделай отчёт к среде", _src(), today=TODAY, sender=leader)

    assert processed[0].task.assignee == "worker"
    assert processed[0].task.team_id == team_b.id
