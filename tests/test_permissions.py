from dirizher.domain.enums import TaskStatus
from dirizher.domain.models import Task, Team, TeamMember
from dirizher.permissions import (
    can_change_task_status,
    can_create_task,
    can_delete_task,
    can_manage_knowledge,
    can_view_member_tasks,
)
from dirizher.repository import TaskRepository, TeamRegistry
from dirizher.state_store import StateStore


def test_superuser_can_be_claimed_only_once_and_is_persisted(tmp_path):
    registry = TeamRegistry()
    first = TeamMember(user_id=1, username="root", full_name="Root")
    second = TeamMember(user_id=2, username="other", full_name="Other")

    assert registry.make_superuser_once(first) is True
    assert registry.get_by_user_id(1).is_superuser is True
    assert registry.make_superuser_once(second) is False
    assert registry.get_by_user_id(2).is_superuser is False

    team = registry.add_team(Team(name="Backend"))
    registry.assign_member_to_team(second, team, leader=True)
    task = Task(title="Ship API", assignee="other", status=TaskStatus.todo, team_id=team.id)

    store = StateStore(str(tmp_path / "state.json"))
    store.save(registry.all(), [task], registry.teams())
    members, tasks, teams = store.load_full()

    assert [m.username for m in members if m.is_superuser] == ["root"]
    assert tasks[0].team_id == team.id
    assert teams[0].manager_user_ids == [2]


def test_role_permissions_are_scoped_to_team_and_own_status():
    registry = TeamRegistry()
    root = registry.grant_superuser(TeamMember(user_id=1, username="root"))
    dev = registry.add_team(Team(name="Dev"))
    ops = registry.add_team(Team(name="Ops"))
    leader = registry.assign_member_to_team(TeamMember(user_id=2, username="lead"), dev, leader=True)
    subordinate = registry.assign_member_to_team(TeamMember(user_id=3, username="worker"), dev)
    outsider_lead = registry.assign_member_to_team(TeamMember(user_id=4, username="opslead"), ops, leader=True)

    task = Task(title="Implement", assignee="worker", team_id=dev.id)

    assert can_create_task(root, task, registry) is True
    assert can_create_task(leader, task, registry) is True
    assert can_create_task(outsider_lead, task, registry) is False
    assert can_create_task(subordinate, task, registry) is False

    assert can_change_task_status(subordinate, task, registry) is True
    assert can_delete_task(subordinate, task, registry) is False
    assert can_delete_task(leader, task, registry) is True
    assert can_delete_task(outsider_lead, task, registry) is False

    assert can_manage_knowledge(subordinate, subordinate.user_id, dev.id) is True
    assert can_manage_knowledge(leader, subordinate.user_id, dev.id) is True
    assert can_manage_knowledge(outsider_lead, subordinate.user_id, dev.id) is False

    # Любой руководитель может смотреть чужие задачи, но права на изменение остаются по команде.
    assert can_view_member_tasks(outsider_lead, subordinate) is True



def test_clear_can_drop_team_structure_but_keep_superuser():
    registry = TeamRegistry()
    root = registry.grant_superuser(TeamMember(user_id=1, username="root"))
    team = registry.add_team(Team(name="Dev"))
    registry.assign_member_to_team(root, team, leader=True)
    registry.assign_member_to_team(TeamMember(user_id=2, username="worker"), team)

    removed = registry.clear(keep_superusers=True, clear_teams=True)

    assert removed == 1
    assert registry.teams() == []
    assert registry.get_by_user_id(1).is_superuser is True
    assert registry.get_by_user_id(1).leader_team_ids == []
    assert registry.get_by_user_id(1).member_team_ids == []


def test_multi_assignee_lookup_and_self_permission():
    registry = TeamRegistry()
    member = registry.register(TeamMember(user_id=10, username="alice", full_name="Alice"))
    task = Task(title="Pair work", assignee="alice и bob")

    repo = TaskRepository()
    repo.add(task)

    assert repo.open_by_assignee("alice") == [task]
    assert can_change_task_status(member, task, registry) is True


async def test_soft_delete_keeps_task_restorable_for_four_hours():
    from datetime import timedelta

    from dirizher.container import AppContainer

    c = AppContainer()
    task = await c.service.create_on_board(Task(title="Trash me", assignee="worker"))

    await c.service.soft_delete_task(task)

    assert c.repo.get(task.id) is task
    assert task.trashed_at is not None
    assert task.delete_after is not None
    assert (task.delete_after - task.trashed_at) == timedelta(hours=4)
    assert c.repo.open() == []
    assert c.repo.trashed() == [task]
    assert len(await c.board.list_cards()) == 1
    assert c.memory.find_duplicate(task.dedup_text()) is None

    await c.service.restore_task(task)

    assert task.trashed_at is None
    assert task.delete_after is None
    assert c.repo.open() == [task]
    assert c.memory.find_duplicate(task.dedup_text()) is not None


async def test_expired_trash_is_purged_from_repo_and_board():
    from datetime import datetime, timedelta, timezone

    from dirizher.container import AppContainer

    c = AppContainer()
    task = await c.service.create_on_board(Task(title="Purge me", assignee="worker"))
    await c.service.soft_delete_task(task)

    removed = await c.service.purge_expired_trash(now=datetime.now(timezone.utc) + timedelta(hours=5))

    assert removed == 1
    assert c.repo.get(task.id) is None
    assert len(await c.board.list_cards()) == 0


def test_restore_does_not_index_trashed_tasks(tmp_path):
    from dirizher.config import Settings
    from dirizher.container import AppContainer

    state_path = tmp_path / "state.json"
    settings = Settings()
    settings.memory.state_path = str(state_path)
    settings.memory.project_snapshot = str(tmp_path / "project.md")
    settings.memory.chroma_path = str(tmp_path / "chroma")
    settings.memory.backend = "lexical"
    settings.yougile.api_key = ""

    c1 = AppContainer(settings)
    task = Task(title="Do not index trash", assignee="worker")
    c1.repo.add(task)
    c1.memory.remember(task.id, task.dedup_text())
    task.trashed_at = task.created_at
    task.delete_after = task.created_at
    c1.memory.forget(task.id)
    c1.persist()

    c2 = AppContainer(settings)

    assert c2.repo.trashed()[0].title == task.title
    assert c2.memory.find_duplicate(task.dedup_text()) is None
