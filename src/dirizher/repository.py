"""Хранилище задач и реестр команды (in-memory).

Намеренно простое и потокобезопасное на уровне asyncio (однопоточный event loop).
Легко заменяется на БД за тем же интерфейсом.
"""

from __future__ import annotations

from datetime import date
import re

from .domain.enums import TaskStatus
from .domain.models import Task, Team, TeamMember

_ASSIGNEE_SPLIT = re.compile(r"\s*(?:,|;|/|&|\bи\b|\band\b)\s*", re.IGNORECASE)


def _assignee_keys(value: str | None) -> set[str]:
    if not value:
        return set()
    parts = [p.lstrip("@").strip().lower() for p in _ASSIGNEE_SPLIT.split(value) if p.strip()]
    return set(parts or [value.lstrip("@").strip().lower()])


class TaskRepository:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    def add(self, task: Task) -> Task:
        self._tasks[task.id] = task
        return task

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def get_by_card(self, card_id: str) -> Task | None:
        for t in self._tasks.values():
            if t.board_card_id == card_id:
                return t
        return None

    def last_created(self) -> Task | None:
        return max(self._tasks.values(), key=lambda t: t.created_at, default=None)

    def all(self) -> list[Task]:
        return list(self._tasks.values())

    def active(self) -> list[Task]:
        return [t for t in self._tasks.values() if t.trashed_at is None]

    def trashed(self) -> list[Task]:
        return [t for t in self._tasks.values() if t.trashed_at is not None]

    def open(self) -> list[Task]:
        return [t for t in self._tasks.values() if t.trashed_at is None and t.status != TaskStatus.done]

    def by_assignee(self, name: str) -> list[Task]:
        key = name.lstrip("@").strip().lower()
        return [
            t for t in self._tasks.values()
            if t.trashed_at is None and key and key in _assignee_keys(t.assignee)
        ]

    def open_by_assignee(self, name: str) -> list[Task]:
        return [t for t in self.by_assignee(name) if t.status != TaskStatus.done]

    @staticmethod
    def in_chat(task: Task, chat_id: int | None) -> bool:
        if chat_id is None:
            return True
        return any(source.chat_id == chat_id for source in task.sources)

    def open_unassigned(self, *, chat_id: int | None = None) -> list[Task]:
        return [
            t for t in self.open()
            if not _assignee_keys(t.assignee) and self.in_chat(t, chat_id)
        ]

    def open_by_assignee_in_chat(self, name: str, chat_id: int | None) -> list[Task]:
        return [t for t in self.open_by_assignee(name) if self.in_chat(t, chat_id)]

    def due_on_or_before(self, day: date) -> list[Task]:
        return [
            t for t in self._tasks.values()
            if t.trashed_at is None and t.deadline and t.deadline <= day and t.status != TaskStatus.done
        ]

    def clear(self) -> int:
        n = len(self._tasks)
        self._tasks.clear()
        return n

    def remove(self, task_id: str) -> None:
        self._tasks.pop(task_id, None)


class TeamRegistry:
    """Реестр участников. Резолвит имя/алиас/username из текста в участника."""

    def __init__(self) -> None:
        self._by_id: dict[int, TeamMember] = {}
        self._anon: list[TeamMember] = []  # без user_id (упомянуты, но не в чате)
        self._teams: dict[str, Team] = {}

    def register(self, member: TeamMember) -> TeamMember:
        if member.user_id is not None:
            existing = self._by_id.get(member.user_id)
            if existing:
                existing.username = member.username or existing.username
                existing.full_name = member.full_name or existing.full_name
                existing.email = member.email or existing.email
                existing.yougile_id = member.yougile_id or existing.yougile_id
                existing.dm_chat_id = member.dm_chat_id or existing.dm_chat_id
                existing.is_superuser = member.is_superuser or existing.is_superuser
                existing.is_no_team_manager = member.is_no_team_manager or existing.is_no_team_manager
                for tid in member.leader_team_ids:
                    if tid not in existing.leader_team_ids:
                        existing.leader_team_ids.append(tid)
                for tid in member.member_team_ids:
                    if tid not in existing.member_team_ids:
                        existing.member_team_ids.append(tid)
                for a in member.aliases:
                    if a not in existing.aliases:
                        existing.aliases.append(a)
                return existing
            self._by_id[member.user_id] = member
            return member
        self._anon.append(member)
        return member

    def knows(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self._by_id

    def clear(self, *, keep_superusers: bool = True, clear_teams: bool = False) -> int:
        """Забыть участников; по умолчанию сохраняем суперюзеров, чтобы бот не осиротел."""
        before = len(self._by_id) + len(self._anon)
        if keep_superusers:
            self._by_id = {uid: m for uid, m in self._by_id.items() if m.is_superuser}
        else:
            self._by_id.clear()
        self._anon.clear()
        if clear_teams:
            self._teams.clear()
            for member in self._by_id.values():
                member.leader_team_ids.clear()
                member.member_team_ids.clear()
                member.is_no_team_manager = False
        else:
            for t in self._teams.values():
                t.manager_user_ids = [uid for uid in t.manager_user_ids if uid in self._by_id]
                t.member_user_ids = [uid for uid in t.member_user_ids if uid in self._by_id]
        return before - (len(self._by_id) + len(self._anon))

    def superuser_exists(self) -> bool:
        return any(m.is_superuser for m in self._by_id.values())

    def make_superuser_once(self, member: TeamMember) -> bool:
        saved = self.register(member)
        if self.superuser_exists():
            return False
        saved.is_superuser = True
        return True

    def grant_superuser(self, member: TeamMember) -> TeamMember:
        saved = self.register(member)
        saved.is_superuser = True
        return saved

    def grant_no_team_manager(self, member: TeamMember) -> TeamMember:
        """Назначить руководителя слоя «нет команды» без добавления в команду."""
        saved = self.register(member)
        saved.is_no_team_manager = True
        return saved

    def add_team(self, team: Team) -> Team:
        self._teams[team.id] = team
        return team

    def teams(self) -> list[Team]:
        return list(self._teams.values())

    def get_team(self, team_id_or_name: str | None) -> Team | None:
        key = (team_id_or_name or "").strip().lower()
        if not key:
            return None
        if key in self._teams:
            return self._teams[key]
        for team in self._teams.values():
            if team.name.lower() == key:
                return team
        return None

    def assign_member_to_team(self, member: TeamMember, team: Team, *, leader: bool = False) -> TeamMember:
        saved = self.register(member)
        if saved.user_id is None:
            return saved
        if saved.user_id not in team.member_user_ids:
            team.member_user_ids.append(saved.user_id)
        if team.id not in saved.member_team_ids:
            saved.member_team_ids.append(team.id)
        if leader:
            if saved.user_id not in team.manager_user_ids:
                team.manager_user_ids.append(saved.user_id)
            if team.id not in saved.leader_team_ids:
                saved.leader_team_ids.append(team.id)
        return saved

    def all(self) -> list[TeamMember]:
        return list(self._by_id.values()) + self._anon

    def get_by_user_id(self, user_id: int | None) -> TeamMember | None:
        if user_id is None:
            return None
        return self._by_id.get(user_id)

    def attach_dm_chat(self, user_id: int, chat_id: int) -> TeamMember | None:
        member = self._by_id.get(user_id)
        if member:
            member.dm_chat_id = chat_id
        return member

    def set_teams(self, teams: list[Team]) -> None:
        self._teams = {t.id: t for t in teams}

    @staticmethod
    def _candidates(m: TeamMember) -> list[str]:
        cands = [m.username or "", m.full_name, *m.aliases]
        if m.full_name:
            cands.append(m.full_name.split()[0])  # имя без фамилии
        return [c.lower() for c in cands if c]

    def resolve(self, name: str | None) -> TeamMember | None:
        """Найти участника по username/имени/алиасу (регистронезависимо). Первый матч."""
        matches = self.resolve_all(name)
        return matches[0] if matches else None

    def resolve_all(self, name: str | None) -> list[TeamMember]:
        """Все участники, подходящие под имя/алиас (для разрешения тёзок, #6)."""
        if not name:
            return []
        key = name.lstrip("@").strip().lower()
        if not key:
            return []
        return [m for m in self.all() if key in self._candidates(m)]

    def mention_for(self, name: str | None) -> str:
        """Готовая @-упоминалка для исполнителя (или просто текстом)."""
        m = self.resolve(name)
        if m:
            return m.mention()
        return f"@{name.lstrip('@')}" if name else "—"
