"""Role and team access helpers for bot actions."""

from __future__ import annotations

import re

from .domain.models import Task, TeamMember
from .repository import TeamRegistry


_ASSIGNEE_SPLIT = re.compile(r"\s*(?:,|;|/|&|\bи\b|\band\b)\s*", re.IGNORECASE)


def _assignee_parts(value: str | None) -> list[str]:
    if not value:
        return []
    return [p.strip() for p in _ASSIGNEE_SPLIT.split(value) if p.strip()] or [value.strip()]


def user_id(member: TeamMember | None) -> int | None:
    return member.user_id if member is not None else None


def is_superuser(member: TeamMember | None) -> bool:
    return bool(member and member.is_superuser)


def is_self(member: TeamMember | None, task: Task, team: TeamRegistry) -> bool:
    if member is None:
        return False
    for assignee in _assignee_parts(task.assignee):
        resolved = team.resolve(assignee)
        if resolved and resolved.user_id == member.user_id:
            return True
        key = assignee.lstrip("@").lower()
        if key and key in {(member.username or "").lower(), (member.full_name or "").lower()}:
            return True
    return False


def task_team_ids(task: Task, team: TeamRegistry) -> list[str]:
    if task.team_id:
        return [task.team_id]
    member = team.resolve(task.assignee)
    if member:
        return list(member.member_team_ids)
    return []


def task_team_id(task: Task, team: TeamRegistry) -> str | None:
    ids = task_team_ids(task, team)
    return ids[0] if ids else None


def _can_manage_no_team(actor: TeamMember | None) -> bool:
    # «Нет команды» — общий слой беседы вне созданных команд. Когда включены роли,
    # управлять им может только отдельный руководитель без команды (и суперюзер
    # выше по стеку проверок). Руководитель обычной команды не получает эти права
    # автоматически, чтобы не назначать задачи людям вне своей команды.
    return bool(actor and actor.is_no_team_manager)


def can_create_task(actor: TeamMember | None, task: Task, team: TeamRegistry) -> bool:
    if not team.superuser_exists():
        return True
    if is_superuser(actor):
        return True
    tids = task_team_ids(task, team)
    if not tids:
        return _can_manage_no_team(actor)
    return bool(actor and any(tid in actor.leader_team_ids for tid in tids))


def can_manage_task(actor: TeamMember | None, task: Task, team: TeamRegistry) -> bool:
    if not team.superuser_exists():
        return True
    if is_superuser(actor):
        return True
    tids = task_team_ids(task, team)
    if not tids:
        return _can_manage_no_team(actor)
    return bool(actor and any(tid in actor.leader_team_ids for tid in tids))


def can_change_task_status(actor: TeamMember | None, task: Task, team: TeamRegistry) -> bool:
    return can_manage_task(actor, task, team) or is_self(actor, task, team)


def can_delete_task(actor: TeamMember | None, task: Task, team: TeamRegistry) -> bool:
    return can_manage_task(actor, task, team)



# Ограничение на просмотр чужих задач. Сейчас ОТКЛЮЧЕНО: через /tasks <тег>
# любой участник может посмотреть задачи любого пользователя. Чтобы вернуть
# роли (только сам/руководитель/суперюзер), поставьте False.
ALLOW_VIEW_ANY_MEMBER_TASKS = True


def can_view_member_tasks(actor: TeamMember | None, target: TeamMember | None) -> bool:
    if actor is None or target is None:
        return False
    if ALLOW_VIEW_ANY_MEMBER_TASKS:
        return True
    if actor.user_id is not None and actor.user_id == target.user_id:
        return True
    return is_superuser(actor) or bool(actor.leader_team_ids) or bool(actor.is_no_team_manager)


def can_manage_knowledge(actor: TeamMember | None, author_user_id: int | None, knowledge_team_id: str | None) -> bool:
    if is_superuser(actor):
        return True
    if actor and author_user_id is not None and actor.user_id == author_user_id:
        return True
    return bool(actor and knowledge_team_id and knowledge_team_id in actor.leader_team_ids)


def can_start_meeting(actor: TeamMember | None) -> bool:
    """Запускать запись созвона могут только руководители и суперюзеры."""
    return is_superuser(actor) or bool(actor and (actor.leader_team_ids or actor.is_no_team_manager))
