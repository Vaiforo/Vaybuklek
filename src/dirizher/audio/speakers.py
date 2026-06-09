"""Реестр голосов: голосовой отпечаток (speaker embedding) → участник.

На первой встрече бот просит представиться и сохраняет эмбеддинг голоса.
Дальше анонимный Speaker_1 мэпится на «Алексея» по близости эмбеддингов —
без обращения к LLM (механика из отчёта, раздел 3.3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..logging_setup import get_logger

log = get_logger("dirizher.audio.speakers")


def _cosine(a: list[float], b: list[float]) -> float:
    import math

    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


@dataclass
class VoicePrint:
    name: str
    embedding: list[float]
    # Сигнатура эмбеддера, создавшего вектор. Векторы разных моделей несравнимы,
    # поэтому матчинг учитывает только отпечатки активной модели. Пусто = legacy.
    model: str = ""


class SpeakerRegistry:
    """Персистентный (JSON) реестр голосовых отпечатков.

    `model` — сигнатура активного эмбеддера (см. embeddings.embedding_signature).
    Сопоставление идёт только с отпечатками этой сигнатуры: при смене модели
    эмбеддингов прежние векторы тихо игнорируются (другая размерность/пространство),
    их нельзя сравнивать — участники просто перерегистрируются.
    """

    def __init__(
        self,
        path: str = "./.data/voiceprints.json",
        threshold: float = 0.75,
        model: str = "",
    ) -> None:
        self._path = Path(path)
        self._threshold = threshold
        self._model = model
        self._prints: list[VoicePrint] = []
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._prints = [VoicePrint(**d) for d in data]

    def _active(self) -> list[VoicePrint]:
        """Отпечатки, сравнимые с текущим эмбеддером (та же сигнатура модели)."""
        return [vp for vp in self._prints if vp.model == self._model]

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps([vp.__dict__ for vp in self._prints], ensure_ascii=False),
            encoding="utf-8",
        )

    # Сколько отпечатков храним на одного человека. Несколько образцов (с разных
    # условий — чистый микрофон, loopback Телемоста) повышают шанс опознать голос,
    # когда тембр «плывёт» из-за кодека связи. Матчинг берёт ЛУЧШИЙ образец.
    _MAX_PER_NAME = 6

    def __len__(self) -> int:
        """Число опознаваемых людей (уникальных имён в активной модели)."""
        return len(set(vp.name for vp in self._active()))

    def names(self) -> list[str]:
        """Уникальные имена (активная модель), в порядке первого появления."""
        seen: list[str] = []
        for vp in self._active():
            if vp.name not in seen:
                seen.append(vp.name)
        return seen

    def sample_count(self, name: str) -> int:
        return sum(1 for vp in self._active() if vp.name == name)

    @property
    def threshold(self) -> float:
        return self._threshold

    def rank(self, embedding: list[float]) -> list[tuple[str, float]]:
        """Известные голоса с косинусной близостью, по убыванию.

        Считаем только по отпечаткам активной модели (векторы разных моделей
        несравнимы). Одно имя — одна запись: берём МАКСИМУМ близости по всем его
        образцам (любой образец достаточно похож → считаем, что это он).
        """
        best: dict[str, float] = {}
        for vp in self._active():
            score = _cosine(embedding, vp.embedding)
            if vp.name not in best or score > best[vp.name]:
                best[vp.name] = score
        ranked = sorted(best.items(), key=lambda x: x[1], reverse=True)
        return ranked

    def enroll(self, name: str, embedding: list[float], *, replace: bool = False) -> int:
        """Добавить образец голоса. По умолчанию НАКАПЛИВАЕМ образцы на человека.

        Образец помечается сигнатурой активной модели. replace=True — заменить все
        прежние образцы этого имени (в активной модели) одним. Возвращает число
        образцов имени после операции. Сверх `_MAX_PER_NAME` отбрасываем старые.
        """
        if replace:
            self._prints = [
                vp for vp in self._prints if not (vp.name == name and vp.model == self._model)
            ]
        self._prints.append(VoicePrint(name=name, embedding=embedding, model=self._model))
        # Ограничиваем число образцов имени в активной модели: последние _MAX_PER_NAME.
        same = [
            vp for vp in self._prints if vp.name == name and vp.model == self._model
        ][-self._MAX_PER_NAME:]
        kept: list[VoicePrint] = []
        for vp in self._prints:
            if vp.name != name or vp.model != self._model or vp in same:
                kept.append(vp)
        self._prints = kept
        self._save()
        count = self.sample_count(name)
        log.info("Голос зарегистрирован: %s (%s, образцов: %d)", name, self._model or "legacy", count)
        return count

    def identify(self, embedding: list[float]) -> str | None:
        """Вернуть имя ближайшего известного голоса или None."""
        ranked = self.rank(embedding)
        if ranked and ranked[0][1] >= self._threshold:
            return ranked[0][0]
        return None
