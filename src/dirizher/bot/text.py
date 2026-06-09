"""Текстовые представления для Telegram (parse_mode=HTML).

Весь пользовательский ввод экранируется: заголовки задач, имена, заметки и
фрагменты базы знаний безопасны для HTML parse mode.
"""

from __future__ import annotations

from datetime import date
from html import escape as esc

from ..domain.enums import TaskStatus
from ..domain.models import Task, TeamMember
from ..services.cabinet import MemberStats
from ..services.task_service import Outcome, ProcessedTask

DIVIDER = "━━━━━━━━━━━━━━"
EMPTY = "—"
MAX_BOARD_ITEMS_PER_STATUS = 12
STATUS_EMOJI = {
    TaskStatus.todo: "📋",
    TaskStatus.in_progress: "▶️",
    TaskStatus.done: "✅",
}


def _section(title: str) -> list[str]:
    return [title, DIVIDER]


def _line(label: str, value: str | None) -> str:
    return f"<b>{label}</b> {esc(value or EMPTY)}"


def render_task_card(task: Task, *, header: str = "🆕 Новая задача") -> str:
    lines = [
        *_section(f"<b>{esc(header)}</b>"),
        f"📋 <b>{esc(task.title)}</b>",
    ]
    if task.requirements:
        lines.append(f"📝 {esc(task.requirements)}")
    lines += [
        _line("👤 Исполнитель:", task.assignee),
        _line("📅 Дедлайн:", task.deadline_display()),
        f"{task.priority.emoji} <b>Приоритет:</b> {esc(task.priority.label_ru)}",
    ]
    if task.board_card_id:
        lines.append(f"🆔 <code>{esc(task.board_card_id)}</code>")
    if task.sources:
        lines.append(f"📎 <b>Источник:</b> {esc(task.sources[0].source.label_ru)}")
    return "\n".join(lines)


def render_processed(p: ProcessedTask) -> str:
    if p.outcome is Outcome.duplicate and p.duplicate_of:
        return (
            "♻️ <b>Похоже, это дубль</b>\n"
            f"{DIVIDER}\n"
            f"Уже есть: <b>{esc(p.duplicate_of.title)}</b>\n"
            f"Новая формулировка: {esc(p.task.title)}\n"
            f"Совпадение: {p.dup_score:.2f}\n\n"
            "Объединить источники или создать отдельную карточку?"
        )
    if p.outcome is Outcome.low_confidence:
        return (
            "🤔 <b>Нужна проверка</b>\n"
            f"{DIVIDER}\n"
            f"Я вижу возможную задачу: <b>{esc(p.task.title)}</b>\n\n"
            "Если это действительно задача — подтвердите. Если нет — отклоните."
        )
    return render_task_card(p.task)


def _render_card_lines(card, number: int) -> list[str]:
    due = card.deadline.isoformat() if getattr(card, "deadline", None) else "без срока"
    assignee = card.assignee or EMPTY
    description = (getattr(card, "description", "") or "").strip()
    title = esc(card.title)
    if len(title) > 96:
        title = title[:93] + "…"
    lines = [
        f"  <b>{number}. {title}</b>",
        f"     🔸 <b>Статус:</b> {esc(card.status.label_ru)}",
        f"     👤 <b>Исполнитель:</b> {esc(assignee)}",
        f"     📅 <b>Дедлайн:</b> {esc(due)}",
    ]
    if description:
        excerpt = description[:137] + "…" if len(description) > 140 else description
        lines.append(f"     📝 <b>Детали:</b> {esc(excerpt)}")
    if getattr(card, "id", None):
        lines.append(f"     🆔 <code>{esc(card.id)}</code>")
    return lines


def render_board(cards) -> str:  # cards: list[BoardCard]
    if not cards:
        return "🗂️ <b>Канбан-доска пуста</b>\nМожно поставить первую задачу прямо сообщением в чат."

    buckets = {status: [] for status in TaskStatus}
    for card in cards:
        buckets[card.status].append(card)

    total = len(cards)
    active = sum(1 for card in cards if card.status is not TaskStatus.done)
    done = total - active
    lines = _section("🗂️ <b>Канбан-доска</b>")
    lines.append(f"Всего: {total} · Активных: {active} · Готово: {done}")
    lines.append("")
    for status in TaskStatus:
        items = sorted(
            buckets[status],
            key=lambda card: (getattr(card, "deadline", None) is None, getattr(card, "deadline", None) or date.max, card.title.lower()),
        )
        emoji = STATUS_EMOJI.get(status, "•")
        lines.append(f"{emoji} <b>{status.label_ru}</b> · {len(items)}")
        if not items:
            lines.append(f"  {EMPTY} пусто")
        else:
            for idx, card in enumerate(items[:MAX_BOARD_ITEMS_PER_STATUS], start=1):
                lines.extend(_render_card_lines(card, idx))
                lines.append("")
            hidden = len(items) - MAX_BOARD_ITEMS_PER_STATUS
            if hidden > 0:
                lines.append(f"  … ещё {hidden}")
        lines.append("")
    return "\n".join(lines).strip()


def render_board_task(card) -> str:  # card: BoardCard
    """Карточка задачи с доски для интерактивного списка «мои задачи»."""
    due = card.deadline.isoformat() if getattr(card, "deadline", None) else "без срока"
    assignee = card.assignee or EMPTY
    lines = [
        f"📋 <b>{esc(card.title)}</b>",
        DIVIDER,
        f"🔸 <b>Статус:</b> {esc(card.status.label_ru)}",
        f"👤 <b>Исполнитель:</b> {esc(assignee)}",
        f"📅 <b>Дедлайн:</b> {esc(due)}",
    ]
    if getattr(card, "id", None):
        lines.append(f"🆔 <code>{esc(card.id)}</code>")
    return "\n".join(lines)


def render_created(task: Task) -> str:
    return (
        "✅ <b>Карточка создана</b>\n"
        f"{DIVIDER}\n"
        f"📋 <b>{esc(task.title)}</b>\n"
        f"👤 {esc(task.assignee or EMPTY)}\n"
        f"📅 {esc(task.deadline_display())}\n"
        f"🆔 <code>{esc(task.board_card_id or '')}</code>"
    )


def render_tasks(tasks: list[Task], *, title: str = "Ваши открытые задачи") -> str:
    if not tasks:
        return f"🎉 <b>{esc(title)}</b>\n{DIVIDER}\nОткрытых задач нет. Можно выдохнуть."
    ordered = sorted(tasks, key=lambda t: (t.deadline is None, t.deadline or date.max, t.title.lower()))
    lines = _section(f"📋 <b>{esc(title)}</b>")
    for idx, task in enumerate(ordered, start=1):
        marker = "🔥" if task.deadline and task.completed_at is None else "•"
        lines.extend([
            f"{marker} <b>{idx}. {esc(task.title)}</b>",
            f"   🔸 {esc(task.status.label_ru)} · {esc(task.priority.label_ru)} {task.priority.emoji}",
            f"   📅 {esc(task.deadline_display())}",
        ])
        if task.assignee:
            lines.append(f"   👤 {esc(task.assignee)}")
    return "\n".join(lines)


def render_personal_digest(member: TeamMember, tasks: list[Task], stats: MemberStats) -> str:
    lines = [
        *_section("🌙 <b>Личная вечерняя сводка</b>"),
        f"👤 {esc(member.full_name or member.username or 'участник')}",
        f"🎮 Уровень {stats.level} · {stats.xp} XP",
        f"📋 Открыто: {stats.open} · ✅ Готово: {stats.done} · 🔥 Просрочено: {stats.overdue}",
        "",
    ]
    if tasks:
        lines.append("<b>Что актуально:</b>")
        for task in tasks[:10]:
            lines.append(f"• {esc(task.title)} — {esc(task.deadline_display())}")
    else:
        lines.append("Открытых задач нет — отличный темп! 🎉")
    return "\n".join(lines)
