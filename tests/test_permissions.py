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


def test_repository_filters_unassigned_and_assigned_tasks_by_chat():
    from dirizher.domain.enums import TaskSource
    from dirizher.domain.models import SourceRef

    repo = TaskRepository()
    chat_one = Task(title="No owner 1", sources=[SourceRef(source=TaskSource.chat, chat_id=100)])
    chat_two = Task(title="No owner 2", sources=[SourceRef(source=TaskSource.chat, chat_id=200)])
    assigned = Task(title="Mine", assignee="alice", sources=[SourceRef(source=TaskSource.chat, chat_id=100)])
    repo.add(chat_one)
    repo.add(chat_two)
    repo.add(assigned)

    assert repo.open_unassigned(chat_id=100) == [chat_one]
    assert {t.id for t in repo.open_unassigned(chat_id=None)} == {chat_one.id, chat_two.id}
    assert repo.open_by_assignee_in_chat("alice", 100) == [assigned]
    assert repo.open_by_assignee_in_chat("alice", 200) == []


def test_no_team_manager_can_create_and_manage_no_team_tasks_when_roles_enabled():
    registry = TeamRegistry()
    registry.grant_superuser(TeamMember(user_id=1, username="root"))
    team = registry.add_team(Team(name="Dev"))
    team_leader = registry.assign_member_to_team(TeamMember(user_id=2, username="lead"), team, leader=True)
    no_team_manager = registry.grant_no_team_manager(TeamMember(user_id=4, username="notm"))
    subordinate = registry.assign_member_to_team(TeamMember(user_id=3, username="worker"), team)
    no_team_task = Task(title="Без исполнителя")

    assert no_team_manager.is_no_team_manager is True
    assert no_team_manager.member_team_ids == []
    assert no_team_manager.leader_team_ids == []
    assert can_create_task(no_team_manager, no_team_task, registry) is True
    assert can_delete_task(no_team_manager, no_team_task, registry) is True
    assert can_view_member_tasks(no_team_manager, subordinate) is True
    assert can_create_task(team_leader, no_team_task, registry) is False
    assert can_delete_task(team_leader, no_team_task, registry) is False
    assert can_create_task(subordinate, no_team_task, registry) is False
    assert can_delete_task(subordinate, no_team_task, registry) is False


def test_permissions_consider_all_member_teams_when_task_team_missing():
    registry = TeamRegistry()
    registry.grant_superuser(TeamMember(user_id=1, username="root"))
    first = registry.add_team(Team(name="First"))
    second = registry.add_team(Team(name="Second"))
    member = registry.register(TeamMember(user_id=2, username="worker"))
    second_leader = registry.assign_member_to_team(TeamMember(user_id=3, username="lead2"), second, leader=True)
    registry.assign_member_to_team(member, first)
    registry.assign_member_to_team(member, second)
    task_without_cached_team = Task(title="Multi-team", assignee="worker")

    assert can_create_task(second_leader, task_without_cached_team, registry) is True
    assert can_delete_task(second_leader, task_without_cached_team, registry) is True


def test_no_team_manager_flag_is_persisted(tmp_path):
    registry = TeamRegistry()
    manager = registry.grant_no_team_manager(TeamMember(user_id=10, username="notm"))
    task = Task(title="No team task")

    store = StateStore(str(tmp_path / "state.json"))
    store.save(registry.all(), [task], registry.teams())
    members, tasks, teams = store.load_full()

    assert members[0].username == manager.username
    assert members[0].is_no_team_manager is True
    assert members[0].member_team_ids == []
    assert members[0].leader_team_ids == []
    assert tasks[0].team_id is None
    assert teams == []
