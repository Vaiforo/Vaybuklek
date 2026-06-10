"""Тесты извлечения задач mock-провайдером (эвристика)."""

from datetime import date

import pytest

from dirizher.domain.enums import Priority
from dirizher.llm.base import ExtractionContext
from dirizher.llm.mock_provider import MockLLMProvider

TODAY = date(2026, 6, 7)  # воскресенье


@pytest.fixture
def ctx():
    return ExtractionContext(today=TODAY)


async def test_basic_task_with_assignee_and_deadline(ctx):
    prov = MockLLMProvider()
    tasks = await prov.extract_tasks("Максим, сделай авторизацию к четвергу", ctx)
    assert len(tasks) == 1
    t = tasks[0]
    assert "авторизаци" in t.task.lower()
    assert t.assignee == "Максим"
    assert t.deadline == date(2026, 6, 11)  # ближайший четверг
    assert t.confidence >= 0.7


async def test_priority_urgent(ctx):
    prov = MockLLMProvider()
    tasks = await prov.extract_tasks("Даша, подготовь макет срочно", ctx)
    assert tasks[0].priority is Priority.high


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("к понедельнику", date(2026, 6, 8)),
        ("к вторнику", date(2026, 6, 9)),
        ("до пятницы", date(2026, 6, 12)),
        ("завтра", date(2026, 6, 8)),
        ("послезавтра", date(2026, 6, 9)),
    ],
)
async def test_deadline_parsing(ctx, phrase, expected):
    prov = MockLLMProvider()
    tasks = await prov.extract_tasks(f"сделай отчёт {phrase}", ctx)
    assert tasks and tasks[0].deadline == expected


# Среда 10.06.2026 — как в реальном кейсе. «Конец недели» = воскресенье ТЕКУЩЕЙ
# недели (14.06), НЕ «через неделю» (17.06). Пятница (12.06) — только «рабочая».
_WED = date(2026, 6, 10)


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("до конца этой недели", date(2026, 6, 14)),  # воскресенье текущей недели
        ("к концу недели", date(2026, 6, 14)),
        ("до конца недели", date(2026, 6, 14)),
        ("на этой неделе", date(2026, 6, 14)),
        ("к выходным", date(2026, 6, 14)),
        ("к концу рабочей недели", date(2026, 6, 12)),  # пятница — только «рабочая»
    ],
)
async def test_end_of_week_parsing(phrase, expected):
    prov = MockLLMProvider()
    ctx = ExtractionContext(today=_WED)
    tasks = await prov.extract_tasks(f"Валера, сделай отчёт {phrase}", ctx)
    assert tasks and tasks[0].deadline == expected
    # фраза срока не должна осесть в заголовке задачи
    assert "недел" not in tasks[0].task.lower()
    assert "выходн" not in tasks[0].task.lower()


async def test_non_task_ignored(ctx):
    prov = MockLLMProvider()
    tasks = await prov.extract_tasks("всем привет, как дела?", ctx)
    assert tasks == []
