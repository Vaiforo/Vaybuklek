"""Тесты персистентности состояния и мульти-исполнителей при правке."""

import os

import pytest

from dirizher.bot.handlers.commands import _card_belongs_to
from dirizher.container import AppContainer
from dirizher.domain.models import Task, Team, TeamMember
from dirizher.integrations.yougile import BoardCard
from dirizher.services.task_service import _is_junk_title
from dirizher.state_store import StateStore


@pytest.fixture
def c():
    cont = AppContainer()
    cont.team.register(TeamMember(user_id=1, username="Stefan_Richards", full_name="Stefan", yougile_id="yg-s"))
    cont.team.register(
        TeamMember(user_id=2, username="danya_skiba", full_name="Данила Скиба",
                   aliases=["Данила"], yougile_id="yg-d")
    )
    return cont


# ── Персистентность ──────────────────────────────────────────────────────────
def test_state_store_roundtrip(tmp_path):
    store = StateStore(str(tmp_path / "s.json"))
    members = [TeamMember(user_id=1, username="u", full_name="U", email="e@e.com",
                          aliases=["псевдо"], yougile_id="yg")]
    tasks = [Task(title="T", assignee="u", assignee_yougile_ids=["yg"])]
    store.save(members, tasks)
    m2, t2 = store.load()
    assert m2[0].email == "e@e.com" and m2[0].yougile_id == "yg" and m2[0].aliases == ["псевдо"]
    assert t2[0].title == "T" and t2[0].assignee_yougile_ids == ["yg"]


def test_container_restores_team_after_restart():
    store = StateStore(os.environ["DIRIZHER_MEMORY__STATE_PATH"])
    store.save([TeamMember(user_id=9, username="zoe", full_name="Zoe", email="z@z.com")], [])
    cont = AppContainer()  # имитируем перезапуск
    assert cont.team.knows(9)
    assert cont.team.resolve("zoe").email == "z@z.com"


def test_state_store_save_without_teams_preserves_existing_team_block(tmp_path):
    store = StateStore(str(tmp_path / "state.json"))
    team = Team(id="dev", name="Dev", manager_user_ids=[1], member_user_ids=[1, 2])
    members = [
        TeamMember(user_id=1, username="lead", leader_team_ids=["dev"], member_team_ids=["dev"]),
        TeamMember(user_id=2, username="worker", member_team_ids=["dev"]),
    ]
    store.save(members, [], [team])

    # Legacy callers used to omit the third arg; that must not wipe org structure.
    store.save(members, [Task(title="T")])

    _members, tasks, teams = store.load_full()
    assert tasks[0].title == "T"
    assert teams == [team]


def test_container_rebuilds_team_list_from_legacy_member_links(tmp_path):
    from dirizher.config import Settings

    state_path = tmp_path / "state.json"
    settings = Settings()
    settings.memory.state_path = str(state_path)
    settings.memory.project_snapshot = str(tmp_path / "project.md")
    settings.memory.chroma_path = str(tmp_path / "chroma")
    settings.memory.backend = "lexical"
    settings.yougile.api_key = ""

    lead = TeamMember(user_id=1, username="lead", leader_team_ids=["legacy"], member_team_ids=["legacy"])
    worker = TeamMember(user_id=2, username="worker", member_team_ids=["legacy"])
    StateStore(str(state_path)).save([lead, worker], [], [])

    cont = AppContainer(settings)

    teams = cont.team.teams()
    assert len(teams) == 1
    assert teams[0].id == "legacy"
    assert set(teams[0].member_user_ids) == {1, 2}
    assert teams[0].manager_user_ids == [1]

    cont.persist()
    _members, _tasks, restored_teams = StateStore(str(state_path)).load_full()
    assert restored_teams[0].id == "legacy"


def test_container_repairs_member_links_from_team_block(tmp_path):
    from dirizher.config import Settings

    state_path = tmp_path / "state.json"
    settings = Settings()
    settings.memory.state_path = str(state_path)
    settings.memory.project_snapshot = str(tmp_path / "project.md")
    settings.memory.chroma_path = str(tmp_path / "chroma")
    settings.memory.backend = "lexical"
    settings.yougile.api_key = ""

    team = Team(id="dev", name="Dev", manager_user_ids=[1], member_user_ids=[1, 2])
    members = [TeamMember(user_id=1, username="lead"), TeamMember(user_id=2, username="worker")]
    StateStore(str(state_path)).save(members, [], [team])

    cont = AppContainer(settings)

    assert cont.team.get_by_user_id(1).member_team_ids == ["dev"]
    assert cont.team.get_by_user_id(1).leader_team_ids == ["dev"]
    assert cont.team.get_by_user_id(2).member_team_ids == ["dev"]


def test_forget_clears_team_and_persists(c):
    assert len(c.team.all()) == 2
    n = c.team.clear()
    c.persist()
    assert n == 2 and c.team.all() == []
    # после перезапуска участники не возвращаются
    reborn = AppContainer()
    assert reborn.team.all() == []


# ── Мульти-исполнители при правке ────────────────────────────────────────────
async def test_correction_assign_multiple_replace(c):
    t = Task(title="встреча с андреем")
    await c.service.apply_correction(t, "назначь @Stefan_Richards и Данила")
    assert set(t.assignee_yougile_ids) == {"yg-s", "yg-d"}
    assert "Stefan_Richards" in t.assignee and "danya_skiba" in t.assignee
    # заголовок не затёрся
    assert t.title == "встреча с андреем"


async def test_correction_add_assignee_appends(c):
    t = Task(title="x", assignee="Stefan_Richards", assignee_yougile_ids=["yg-s"])
    await c.service.apply_correction(t, "добавь к исполнителям @danya_skiba")
    assert t.assignee_yougile_ids == ["yg-s", "yg-d"]
    assert "Stefan_Richards" in t.assignee and "danya_skiba" in t.assignee


async def test_correction_unrelated_keeps_assignee(c):
    t = Task(title="x", assignee="Stefan_Richards", assignee_yougile_ids=["yg-s"])
    await c.service.apply_correction(t, "перенеси на пятницу")
    assert t.assignee == "Stefan_Richards" and t.assignee_yougile_ids == ["yg-s"]


# ── Анти-мусор: заголовок-команда ────────────────────────────────────────────
@pytest.mark.parametrize("title", ["создать задачу", "Поставить задачу", "на доску", "задача", "таск"])
def test_junk_titles_rejected(title):
    assert _is_junk_title(title) is True


@pytest.mark.parametrize("title", ["созвон с Андреем", "фикс API", "сделать презентацию"])
def test_real_titles_kept(title):
    assert _is_junk_title(title) is False


# ── /tasks не путает исполнителей ────────────────────────────────────────────
def test_card_belongs_by_yougile_id():
    m = TeamMember(user_id=1, username="stefan", full_name="Stefan", yougile_id="yg-s")
    assert _card_belongs_to(BoardCard(id="1", title="t", assignee_ids=["yg-s"]), m) is True
    # чужая карточка (другой id, другое имя) — не моя
    assert _card_belongs_to(
        BoardCard(id="2", title="t", assignee="Данила", assignee_ids=["yg-d"]), m
    ) is False
