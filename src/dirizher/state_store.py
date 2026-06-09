"""Персистентное состояние: команда + задачи в одном JSON-файле.

Зачем: TeamRegistry и TaskRepository живут в памяти, поэтому после перезапуска
бот «забывал» почту, алиасы и привязки к доске. Этот стор сериализует команду и
задачи и поднимает их обратно на старте — без БД, атомарной записью.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .domain.models import Task, Team, TeamMember
from .logging_setup import get_logger

log = get_logger("dirizher.state")


class StateStore:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> tuple[list[TeamMember], list[Task]]:
        members, tasks, _teams = self.load_full()
        return members, tasks

    def load_full(self) -> tuple[list[TeamMember], list[Task], list[Team]]:
        if not self._path.exists():
            return [], [], []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось прочитать состояние (%s) — старт с чистого", e)
            return [], [], []
        members: list[TeamMember] = []
        for m in data.get("team", []):
            try:
                members.append(TeamMember(**m))
            except Exception as e:  # noqa: BLE001
                log.warning("Пропущен участник из состояния: %s", e)
        tasks: list[Task] = []
        for t in data.get("tasks", []):
            try:
                tasks.append(Task(**t))
            except Exception as e:  # noqa: BLE001
                log.warning("Пропущена задача из состояния: %s", e)
        teams: list[Team] = []
        for tm in data.get("teams", []):
            try:
                teams.append(Team(**tm))
            except Exception as e:  # noqa: BLE001
                log.warning("Пропущена команда из состояния: %s", e)
        log.info("Состояние загружено: участников %d, задач %d, команд %d", len(members), len(tasks), len(teams))
        return members, tasks, teams

    def save(self, members: list[TeamMember], tasks: list[Task], teams: list[Team] | None = None) -> None:
        # Backward-compatible API: older code called save(members, tasks) before
        # org-structure was stored as a separate top-level `teams` block. Do not
        # treat omitted `teams` as "clear all teams", otherwise a partial save can
        # leave users in state while wiping the team list. Passing [] explicitly
        # still clears teams (used by reset flows).
        if teams is None:
            _members, _tasks, existing_teams = self.load_full()
            teams = existing_teams
        data = {
            "team": [m.model_dump(mode="json") for m in members],
            "tasks": [t.model_dump(mode="json") for t in tasks],
            "teams": [t.model_dump(mode="json") for t in teams],
        }
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        # атомарно: пишем во временный файл и заменяем — не оставляем «битый» файл
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, self._path)
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось сохранить состояние: %s", e)
            if os.path.exists(tmp):
                os.unlink(tmp)
