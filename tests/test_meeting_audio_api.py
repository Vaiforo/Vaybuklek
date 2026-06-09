"""Эндпоинт POST /meeting/audio — приём аудио созвона из браузерного расширения.

Конвейер не гоняем: мокаем _process_recording и перекодировку, проверяем только
контракт эндпоинта (авторизация, валидация, готовность бота).
"""

from __future__ import annotations

import dirizher.audio.ingest as ingest_mod
import dirizher.bot.handlers.meeting as meeting_mod
from dirizher.api.server import create_api
from dirizher.config import Settings
from dirizher.container import AppContainer
from fastapi.testclient import TestClient


def _container(secret: str = "") -> AppContainer:
    s = Settings()
    s.api.shared_secret = secret
    return AppContainer(s)


def _patch_pipeline(monkeypatch) -> list:
    """Заглушить тяжёлую обработку; вернуть список вызовов _process_recording."""
    calls: list = []

    async def fake_process(c, bot, chat_id, path, reason):
        calls.append((chat_id, path, reason))

    monkeypatch.setattr(meeting_mod, "_process_recording", fake_process)
    monkeypatch.setattr(ingest_mod, "to_wav16k_mono", lambda p: p)
    return calls


def test_health_ping_ok(monkeypatch):
    c = _container()
    client = TestClient(create_api(c))
    r = client.get("/meeting/audio/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["transcriber"] == "mock"


def test_meeting_audio_requires_token(monkeypatch):
    _patch_pipeline(monkeypatch)
    c = _container(secret="s3cret")
    c.bot = object()  # бот «запущен»
    client = TestClient(create_api(c))
    r = client.post(
        "/meeting/audio",
        files={"file": ("a.webm", b"data", "audio/webm")},
        data={"chat_id": "42"},
        headers={"X-Dirizher-Token": "wrong"},
    )
    assert r.status_code == 401


def test_meeting_audio_missing_chat_id(monkeypatch):
    _patch_pipeline(monkeypatch)
    c = _container()
    c.bot = object()
    client = TestClient(create_api(c))
    r = client.post("/meeting/audio", files={"file": ("a.webm", b"data", "audio/webm")})
    assert r.status_code == 422


def test_meeting_audio_bot_not_running(monkeypatch):
    _patch_pipeline(monkeypatch)
    c = _container()
    c.bot = None
    client = TestClient(create_api(c))
    r = client.post(
        "/meeting/audio",
        files={"file": ("a.webm", b"data", "audio/webm")},
        data={"chat_id": "42"},
    )
    assert r.status_code == 503


def test_meeting_command_reflects_signal(monkeypatch):
    c = _container()
    client = TestClient(create_api(c))
    # По умолчанию — стоять.
    r = client.get("/meeting/command", params={"chat_id": 42})
    assert r.status_code == 200
    body = r.json()
    assert body["desired"] == "idle"
    assert body["silence_seconds"] == c.settings.audio.meeting_silence_seconds
    assert body["silence_rms"] == c.settings.audio.meeting_silence_rms
    # Подняли сигнал записи — эндпоинт это отдаёт.
    c.extension_signal.want_record(42)
    assert client.get("/meeting/command", params={"chat_id": 42}).json()["desired"] == "recording"


def test_meeting_command_requires_token(monkeypatch):
    c = _container(secret="s3cret")
    client = TestClient(create_api(c))
    r = client.get("/meeting/command", params={"chat_id": 42}, headers={"X-Dirizher-Token": "nope"})
    assert r.status_code == 401


def test_meeting_audio_resets_signal_to_idle(monkeypatch):
    _patch_pipeline(monkeypatch)
    c = _container()
    c.bot = object()
    c.extension_signal.want_record(42)
    client = TestClient(create_api(c))
    r = client.post(
        "/meeting/audio",
        files={"file": ("a.webm", b"bytes", "audio/webm")},
        data={"chat_id": "42"},
    )
    assert r.status_code == 200
    # Приём записи гасит сигнал, чтобы расширение не зациклило автозапуск.
    assert c.extension_signal.desired(42) == "idle"


def test_meeting_audio_accepts_valid(monkeypatch):
    _patch_pipeline(monkeypatch)
    c = _container(secret="s3cret")
    c.bot = object()
    client = TestClient(create_api(c))
    r = client.post(
        "/meeting/audio",
        files={"file": ("a.webm", b"hello-bytes", "audio/webm")},
        data={"chat_id": "42", "reason": "extension"},
        headers={"X-Dirizher-Token": "s3cret"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "processing"
    assert body["bytes"] == len(b"hello-bytes")
