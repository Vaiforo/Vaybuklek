"""Личный кабинет, метрики, заметки, база знаний и игровые достижения.

Сервис in-memory: для прототипа этого достаточно, а позже его можно заменить
на БД без изменения Telegram-команд.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from html import escape as esc

from ..domain.enums import TaskStatus
from ..domain.models import TeamMember
from ..repository import TaskRepository, TeamRegistry
from .gamification import rank_for, xp_breakdown_for_completion, is_on_time

_TOKEN_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)
DIVIDER = "━━━━━━━━━━━━━━"
EMPTY = "—"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text) if len(t) > 2}


def _key(name: str | None) -> str:
    return (name or "").lstrip("@").strip().lower()


@dataclass
class PersonalNote:
    id: int
    text: str
    created_at: datetime = field(default_factory=_now)


@dataclass
class KnowledgeItem:
    id: int
    title: str
    text: str
    author: str
    author_user_id: int | None = None
    team_id: str | None = None
    created_at: datetime = field(default_factory=_now)


@dataclass
class MemberStats:
    total: int
    open: int
    done: int
    in_progress: int
    overdue: int
    on_time_done: int
    late_done: int
    avg_cycle_hours: float | None
    xp: int
    level: int
    achievements: list[str]

    @property
    def on_time_rate(self) -> int:
        completed_with_deadline = self.on_time_done + self.late_done
        if completed_with_deadline == 0:
            return 0
        return round(self.on_time_done / completed_with_deadline * 100)


class PersonalCabinet:
    """Мини-CRM участника и команды: заметки, знания, метрики, RPG-профиль."""

    def __init__(self, repo: TaskRepository, team: TeamRegistry) -> None:
        self.repo = repo
        self.team = team
        self._notes: dict[str, list[PersonalNote]] = {}
        self._knowledge: list[KnowledgeItem] = []
        self._note_seq = 1
        self._knowledge_seq = 1
        self._reports: dict[str, int] = {}

    def member_key(self, member_or_name: TeamMember | str | None) -> str:
        if isinstance(member_or_name, TeamMember):
            return _key(member_or_name.username or member_or_name.full_name)
        return _key(member_or_name)

    def add_note(self, member_or_name: TeamMember | str | None, text: str) -> PersonalNote:
        note = PersonalNote(id=self._note_seq, text=text.strip())
        self._note_seq += 1
        self._notes.setdefault(self.member_key(member_or_name), []).append(note)
        return note

    def notes_for(self, member_or_name: TeamMember | str | None, limit: int | None = 8) -> list[PersonalNote]:
        notes = self._notes.get(self.member_key(member_or_name), [])
        return list(notes if limit is None else notes[-limit:])

    def edit_note(self, member_or_name: TeamMember | str | None, note_id: int, text: str) -> PersonalNote | None:
        notes = self._notes.get(self.member_key(member_or_name), [])
        for note in notes:
            if note.id == note_id:
                note.text = text.strip()
                return note
        return None

    def delete_note(self, member_or_name: TeamMember | str | None, note_id: int) -> PersonalNote | None:
        notes = self._notes.get(self.member_key(member_or_name), [])
        for i, note in enumerate(notes):
            if note.id == note_id:
                return notes.pop(i)
        return None

    def clear_notes(self, member_or_name: TeamMember | str | None) -> int:
        key = self.member_key(member_or_name)
        removed = len(self._notes.get(key, []))
        self._notes[key] = []
        return removed

    def record_report(self, member_or_name: TeamMember | str | None) -> None:
        key = self.member_key(member_or_name)
        if key:
            self._reports[key] = self._reports.get(key, 0) + 1

    def add_knowledge(
        self, title: str, text: str, author: str, *, author_user_id: int | None = None, team_id: str | None = None
    ) -> KnowledgeItem:
        item = KnowledgeItem(
            id=self._knowledge_seq,
            title=title.strip()[:80] or "Заметка команды",
            text=text.strip(),
            author=author,
            author_user_id=author_user_id,
            team_id=team_id,
        )
        self._knowledge_seq += 1
        self._knowledge.append(item)
        return item

    def get_knowledge(self, item_id: int) -> KnowledgeItem | None:
        for item in self._knowledge:
            if item.id == item_id:
                return item
        return None

    def edit_knowledge(self, item_id: int, title: str, text: str, author: str | None = None) -> KnowledgeItem | None:
        item = self.get_knowledge(item_id)
        if item is None:
            return None
        item.title = title.strip()[:80] or item.title
        item.text = text.strip()
        if author is not None:
            item.author = author
        return item

    def delete_knowledge(self, item_id: int) -> KnowledgeItem | None:
        for i, item in enumerate(self._knowledge):
            if item.id == item_id:
                return self._knowledge.pop(i)
        return None

    def clear_knowledge(self) -> int:
        removed = len(self._knowledge)
        self._knowledge.clear()
        return removed

    def recent_knowledge(self, limit: int = 5) -> list[KnowledgeItem]:
        return self._knowledge[-limit:]

    def search_knowledge(self, query: str, limit: int = 5) -> list[KnowledgeItem]:
        q = _tokens(query)
        if not q:
            return self.recent_knowledge(limit)
        ranked: list[tuple[int, KnowledgeItem]] = []
        for item in self._knowledge:
            score = len(q & (_tokens(item.title) | _tokens(item.text)))
            if score:
                ranked.append((score, item))
        ranked.sort(key=lambda pair: (pair[0], pair[1].created_at), reverse=True)
        return [item for _score, item in ranked[:limit]]

    def stats_for(self, member_or_name: TeamMember | str | None, *, today: date | None = None) -> MemberStats:
        today = today or date.today()
        key = self.member_key(member_or_name)
        tasks = self.repo.by_assignee(key) if key else []
        done = [t for t in tasks if t.status is TaskStatus.done]
        open_tasks = [t for t in tasks if t.status is not TaskStatus.done]
        in_progress = [t for t in tasks if t.status is TaskStatus.in_progress]
        overdue = [t for t in open_tasks if t.deadline and t.deadline < today]

        def completion_day(t) -> date | None:
            if not t.completed_at:
                return None
            done_day = t.completed_at.date()
            # stats_for(today=...) часто используется в тестах/сводках как «срез на дату».
            # Если системные часы позже этого среза, не превращаем закрытую задачу в
            # ложное «просрочено»: считаем закрытие не позже даты среза.
            return min(done_day, today)

        on_time_done = [t for t in done if t.deadline and (day := completion_day(t)) and day <= t.deadline]
        late_done = [t for t in done if t.deadline and (day := completion_day(t)) and day > t.deadline]

        cycle_hours: list[float] = []
        for t in done:
            if t.completed_at:
                cycle_hours.append((t.completed_at - t.created_at).total_seconds() / 3600)
        avg_cycle = round(sum(cycle_hours) / len(cycle_hours), 1) if cycle_hours else None

        notes = len(self._notes.get(key, []))
        authored_kb = sum(1 for item in self._knowledge if _key(item.author) == key)
        reports = self._reports.get(key, 0)
        # XP считается тем же балансом, что и игровая система: очки дают закрытые
        # задачи, а заметки/отчёты/БЗ влияют на кабинетные ачивки, но не накручивают
        # лидербордную валюту.
        xp = sum(
            xp_breakdown_for_completion(t, on_time=is_on_time(t, day)).total
            for t in done
            if (day := completion_day(t))
        )
        level = rank_for(xp)[0]
        achievements = self._achievements(
            done=len(done),
            open_count=len(open_tasks),
            on_time=len(on_time_done),
            notes=notes,
            authored_kb=authored_kb,
            reports=reports,
        )
        return MemberStats(
            total=len(tasks),
            open=len(open_tasks),
            done=len(done),
            in_progress=len(in_progress),
            overdue=len(overdue),
            on_time_done=len(on_time_done),
            late_done=len(late_done),
            avg_cycle_hours=avg_cycle,
            xp=xp,
            level=level,
            achievements=achievements,
        )

    @staticmethod
    def _achievements(
        *, done: int, open_count: int, on_time: int, notes: int, authored_kb: int, reports: int
    ) -> list[str]:
        achievements: list[str] = []
        if done >= 1:
            achievements.append("🥉 Первый закрытый таск")
        if done >= 5:
            achievements.append("🥈 Спринт-финишер")
        if done >= 10:
            achievements.append("🥇 Машина продуктивности")
        if on_time >= 3:
            achievements.append("⏱️ Хранитель дедлайнов")
        if open_count == 0 and done > 0:
            achievements.append("🧘 Чистая доска")
        if notes >= 3:
            achievements.append("🗒️ Системный мыслитель")
        if authored_kb >= 1:
            achievements.append("📚 Хранитель знаний")
        if reports >= 3:
            achievements.append("🌙 Ритуал отчётов")
        return achievements or ["🌱 Новичок оркестра"]

    def render_profile(self, member: TeamMember) -> str:
        stats = self.stats_for(member)
        notes = self.notes_for(member, limit=3)
        avg_cycle = f"{stats.avg_cycle_hours} ч" if stats.avg_cycle_hours is not None else EMPTY
        lines = [
            "👤 <b>Личный кабинет</b>",
            DIVIDER,
            f"<b>{esc(member.full_name or member.mention())}</b>"
            + (f" · @{esc(member.username)}" if member.username else ""),
            "",
            "📊 <b>Задачи</b>",
            f"• Открыто: <b>{stats.open}</b> · В работе: <b>{stats.in_progress}</b> · Готово: <b>{stats.done}</b>",
            f"• Просрочено: <b>{stats.overdue}</b> · В срок: <b>{stats.on_time_rate}%</b>",
            f"• Средний цикл: <b>{avg_cycle}</b>",
            "",
            "🎮 <b>Прогресс</b>",
            f"• Уровень: <b>{stats.level}</b> · XP за закрытия: <b>{stats.xp}</b>",
            "",
            "🏅 <b>Ачивки</b>",
            *[f"• {esc(a)}" for a in stats.achievements[:5]],
        ]
        if notes:
            lines += ["", "🗒️ <b>Последние заметки</b>"]
            lines += [f"• #{n.id} {esc(n.text)}" for n in notes]
        return "\n".join(lines)

    def render_notes(self, member: TeamMember) -> str:
        notes = self.notes_for(member, limit=10)
        if not notes:
            return "🗒️ Заметок пока нет. Добавьте: <code>/note текст заметки</code>"
        lines = [
            f"🗒️ <b>Ваши заметки</b> · {len(notes)}",
            DIVIDER,
            "✏️ <code>/note_edit ID текст</code> · 🗑️ <code>/note_del ID</code> · 🧹 <code>/notes_clear</code>",
            "",
        ]
        for n in notes:
            lines.append(f"<b>#{n.id}</b> · {n.created_at.date().isoformat()}")
            lines.append(f"{esc(n.text)}")
            lines.append("")
        return "\n".join(lines).strip()

    def render_knowledge(self, items: list[KnowledgeItem]) -> str:
        if not items:
            return "📚 В базе знаний пока ничего не найдено. Добавьте: <code>/kb add Заголовок | текст</code>"
        lines = [
            f"📚 <b>База знаний команды</b> · {len(items)}",
            DIVIDER,
            "✏️ <code>/kb edit ID Заголовок | текст</code> · 🗑️ <code>/kb del ID</code> · 🧹 <code>/kb clear</code>",
            "",
        ]
        for item in items:
            lines.append(f"<b>#{item.id} · {esc(item.title)}</b>")
            lines.append(f"{esc(item.text)}")
            lines.append(f"👤 {esc(item.author)} · {item.created_at.date().isoformat()}")
            lines.append("")
        return "\n".join(lines).strip()
