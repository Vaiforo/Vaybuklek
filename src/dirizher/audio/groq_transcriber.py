"""Распознавание речи через Groq Whisper API (без локальных моделей).

Groq хостит Whisper (`whisper-large-v3-turbo`) — это быстрый облачный STT,
который не требует ffmpeg/torch/faster-whisper на машине. Аудио из Telegram
(.oga/.ogg/.mp4) Groq принимает как есть. Ключи переиспользуются те же, что и
для LLM, с такой же ротацией при 429 (см. GroqLLMProvider).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from importlib.util import find_spec

from ..logging_setup import get_logger
from .transcriber import Segment, TranscriptResult


class FallbackRateLimitError(Exception):
    """Совместимый fallback для тестов и mock-режима без пакета groq."""

    def __init__(self, message: str, response=None, body=None) -> None:
        super().__init__(message)
        self.response = response
        self.body = body


class FallbackAsyncGroq:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.audio = None


HAS_GROQ = find_spec("groq") is not None

if HAS_GROQ:
    from groq import AsyncGroq, RateLimitError
else:
    AsyncGroq = FallbackAsyncGroq
    RateLimitError = FallbackRateLimitError

log = get_logger("dirizher.audio.groq")


class GroqWhisperTranscriber:
    name = "groq-whisper"

    def __init__(self, api_keys: list[str], model: str) -> None:
        self._clients = [AsyncGroq(api_key=k) for k in api_keys]
        self._model = model
        self._idx = 0  # индекс текущего рабочего ключа
        log.info("Groq Whisper (%s): ключей в ротации — %d", model, len(self._clients))

    async def transcribe(self, file_path: str) -> TranscriptResult:
        data = await asyncio.to_thread(Path(file_path).read_bytes)
        filename = os.path.basename(file_path) or "audio.ogg"

        last_err: Exception | None = None
        for _ in range(len(self._clients)):
            client = self._clients[self._idx]
            try:
                # verbose_json + посегментные таймкоды нужны для диаризации по
                # голосу (разделение реплик участников встречи). Плоский текст
                # без таймкодов разделить по говорящим невозможно.
                resp = await client.audio.transcriptions.create(
                    file=(filename, data),
                    model=self._model,
                    language="ru",
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                )
                text = (getattr(resp, "text", None) or "").strip()
                segments = _parse_segments(resp)
                return TranscriptResult(text=text, segments=segments, is_mock=False)
            except RateLimitError as e:
                last_err = e
                n = len(self._clients)
                log.warning("Groq Whisper ключ #%d/%d исчерпан (429) — переключаюсь", self._idx + 1, n)
                self._idx = (self._idx + 1) % n
                continue
        raise last_err if last_err else RuntimeError("Groq Whisper: нет доступных ключей")


def _parse_segments(resp) -> list[Segment]:
    """Достать посегментную разметку из ответа Groq (verbose_json).

    Сегменты могут прийти объектами или dict'ами (зависит от версии SDK), а на
    старом плоском ответе/в тестах их нет — тогда возвращаем пустой список, и
    диаризация по голосу просто не запустится.
    """
    raw = getattr(resp, "segments", None)
    if not raw:
        return []

    def field(item, key):
        return item.get(key) if isinstance(item, dict) else getattr(item, key, None)

    out: list[Segment] = []
    for item in raw:
        text = (field(item, "text") or "").strip()
        if not text:
            continue
        start, end = field(item, "start"), field(item, "end")
        out.append(
            Segment(
                speaker="Speaker_1",
                text=text,
                start=float(start) if start is not None else None,
                end=float(end) if end is not None else None,
            )
        )
    return out
