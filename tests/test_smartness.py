"""Тесты «умных» улучшений: fuzzy-резолв исполнителя (#6) и
семантический матчинг отчёт↔задача (#4)."""

from datetime import date

import pytest

from dirizher.container import AppContainer
from dirizher.domain.enums import TaskSource, TaskStatus
from dirizher.domain.models import SourceRef, TeamMember
from dirizher.memory.vector_store import TaskMemory

TODAY = date(2026, 6, 7)
CHAT = 777


# ── #6 fuzzy-резолвинг ────────────────────────────────────────────────────────
def test_resolve_exact_still_wins():
    c = AppContainer()
    c.team.register(TeamMember(user_id=1, username="maxim", full_name="Максим"))
    assert c.team.resolve("Максим").username == "maxim"
    assert c.team.resolve("@maxim").username == "maxim"


def test_resolve_fuzzy_handles_typo():
    c = AppContainer()
    c.team.register(TeamMember(user_id=1, username="maxim", full_name="Максим"))
    # опечатка в имени → всё равно находим
    assert c.team.resolve("Максмм").username == "maxim"


def test_resolve_fuzzy_does_not_overmatch_strangers():
    c = AppContainer()
    c.team.register(TeamMember(user_id=1, username="maxim", full_name="Максим"))
    # совсем другое имя не должно приклеиться к Максиму
    assert c.team.resolve("Владислав") is None


def test_resolve_short_names_skip_fuzzy():
    c = AppContainer()
    c.team.register(TeamMember(user_id=1, username="maxim", full_name="Максим"))
    assert c.team.resolve("ок") is None  # слишком короткое — без фуззи


# ── #4 семантический матчинг отчёта к задаче ──────────────────────────────────
def test_lexical_pairwise_similarity():
    m = TaskMemory("./.data/_t", 0.75, backend="lexical")
    high = m.pairwise_similarity("сделать авторизацию", "авторизацию доделал")
    low = m.pairwise_similarity("сделать авторизацию", "купить молоко")
    assert high > low


class _FakeMemory:
    """Память, где близость высока только для конкретной задачи."""

    def __init__(self, target_title: str) -> None:
        self._target = target_title

    def pairwise_similarity(self, a: str, b: str) -> float:
        return 0.9 if b == self._target else 0.1


@pytest.fixture
async def two_tasks():
    c = AppContainer()
    c.team.register(TeamMember(user_id=1, username="maxim", full_name="Максим"))
    titles = []
    for msg in ["Максим, сделай авторизацию к пятнице",
                "Максим, сделай макет к пятнице"]:
        p = (await c.service.ingest(msg, SourceRef(source=TaskSource.chat, chat_id=CHAT), today=TODAY))[0]
        created = await c.service.create_on_board(p.task)
        titles.append(created.title)
    return c, titles


async def test_semantic_match_picks_right_task(two_tasks):
    c, titles = two_tasks
    target = titles[0]  # «...авторизации»
    c.reconciliation.memory = _FakeMemory(target)
    # отчёт без общих слов с задачей — матч только через семантику
    notes = await c.reconciliation.record_report(CHAT, "maxim", "закрыл вчерашнюю штуку, всё ок", today=TODAY)
    assert notes
    by_title = {t.title: t.status for t in c.repo.by_assignee("maxim")}
    assert by_title[target] is TaskStatus.done
    # вторая задача не должна закрыться
    other = titles[1]
    assert by_title[other] is not TaskStatus.done
