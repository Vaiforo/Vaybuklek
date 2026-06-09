"""Тесты слоя распознавания речи (голосовые/кружки → текст)."""

from __future__ import annotations

from pathlib import Path

import httpx

from dirizher.audio import groq_transcriber
from dirizher.audio.groq_transcriber import GroqWhisperTranscriber
from dirizher.audio.transcriber import MockTranscriber, build_transcriber
from dirizher.config import AudioSettings


# ── Выбор бэкенда ────────────────────────────────────────────────────────────
def test_disabled_returns_mock():
    t = build_transcriber(AudioSettings(enabled=False))
    assert isinstance(t, MockTranscriber)


def test_groq_backend_with_own_keys(monkeypatch):
    monkeypatch.setattr(groq_transcriber, "HAS_GROQ", True)
    cfg = AudioSettings(enabled=True, backend="groq", groq_api_key="k1")
    t = build_transcriber(cfg)
    assert t.name == "groq-whisper"


def test_groq_backend_falls_back_to_llm_keys(monkeypatch):
    monkeypatch.setattr(groq_transcriber, "HAS_GROQ", True)
    cfg = AudioSettings(enabled=True, backend="groq")  # своих ключей нет
    t = build_transcriber(cfg, fallback_groq_keys=["llm-key"])
    assert t.name == "groq-whisper"


def test_groq_backend_without_package_returns_mock(monkeypatch):
    monkeypatch.setattr(groq_transcriber, "HAS_GROQ", False)
    cfg = AudioSettings(enabled=True, backend="groq", groq_api_key="k1")
    assert isinstance(build_transcriber(cfg), MockTranscriber)


def test_groq_backend_no_keys_returns_mock():
    cfg = AudioSettings(enabled=True, backend="groq")
    t = build_transcriber(cfg, fallback_groq_keys=[])
    assert isinstance(t, MockTranscriber)


def test_groq_key_list_dedup_and_strip():
    cfg = AudioSettings(groq_api_key=" a ", groq_api_keys="b, a ,, c")
    assert cfg.groq_key_list == ["a", "b", "c"]


# ── Распознавание через Groq (с поддельным клиентом, без сети) ────────────────
class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeTranscriptions:
    def __init__(self, text: str | None = None, exc: Exception | None = None) -> None:
        self._text = text
        self._exc = exc
        self.calls = 0

    async def create(self, **_kw):
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return _Resp(self._text or "")


class _FakeAudio:
    def __init__(self, transcriptions):
        self.transcriptions = transcriptions


class _FakeClient:
    def __init__(self, text=None, exc=None):
        self.audio = _FakeAudio(_FakeTranscriptions(text, exc))


def _make(tmp_path) -> str:
    p = Path(tmp_path) / "voice.oga"
    p.write_bytes(b"\x00\x01fake-opus")
    return str(p)


async def test_groq_transcribe_happy_path(tmp_path):
    t = GroqWhisperTranscriber(["k1"], "whisper-large-v3-turbo")
    t._clients = [_FakeClient(text="  сделать отчёт к пятнице ")]
    res = await t.transcribe(_make(tmp_path))
    assert res.text == "сделать отчёт к пятнице"
    assert res.is_mock is False


class _VerboseResp:
    """Ответ Groq в формате verbose_json: текст + посегментные таймкоды."""

    def __init__(self, text: str, segments: list[dict]) -> None:
        self.text = text
        self.segments = segments


async def test_groq_transcribe_returns_timed_segments(tmp_path):
    """verbose_json → сегменты с таймкодами (нужны диаризации по голосу)."""
    resp = _VerboseResp(
        "привет всем готов отчёт",
        [
            {"start": 0.0, "end": 1.5, "text": " привет всем"},
            {"start": 1.5, "end": 3.0, "text": "готов отчёт"},
            {"start": 3.0, "end": 3.1, "text": "   "},  # пустой — отбрасываем
        ],
    )
    t = GroqWhisperTranscriber(["k1"], "m")
    client = _FakeClient()
    client.audio.transcriptions._resp = resp  # подменяем ответ create()

    async def _create(**_kw):
        client.audio.transcriptions.calls += 1
        return resp

    client.audio.transcriptions.create = _create
    t._clients = [client]

    res = await t.transcribe(_make(tmp_path))
    assert [s.text for s in res.segments] == ["привет всем", "готов отчёт"]
    assert res.segments[0].start == 0.0 and res.segments[1].end == 3.0
    assert all(s.speaker == "Speaker_1" for s in res.segments)


def test_join_consecutive_groups_by_speaker():
    from dirizher.audio.pipeline import WhisperPipeline
    from dirizher.audio.transcriber import Segment

    segs = [
        Segment(speaker="Speaker_1", text="привет"),
        Segment(speaker="Speaker_1", text="у нас встреча"),
        Segment(speaker="Speaker_2", text="да, я готовлю отчёт"),
        Segment(speaker="Speaker_1", text="отлично"),
    ]
    out = WhisperPipeline._join_consecutive(segs)
    assert out == (
        "Speaker_1: привет у нас встреча\n"
        "Speaker_2: да, я готовлю отчёт\n"
        "Speaker_1: отлично"
    )


async def test_groq_transcribe_rotates_on_rate_limit(tmp_path):
    from dirizher.audio.groq_transcriber import RateLimitError

    err = RateLimitError(
        "limit",
        response=httpx.Response(429, request=httpx.Request("POST", "https://api.groq.com")),
        body=None,
    )
    t = GroqWhisperTranscriber(["k1", "k2"], "m")
    exhausted = _FakeClient(exc=err)
    ok = _FakeClient(text="готово")
    t._clients = [exhausted, ok]
    res = await t.transcribe(_make(tmp_path))
    assert res.text == "готово"
    # первый ключ дал 429, второй сработал
    assert exhausted.audio.transcriptions.calls == 1
    assert ok.audio.transcriptions.calls == 1
