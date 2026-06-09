"""Выбор источника звука встреч: конфиг, per-chat store, парсинг ссылки/команды.

Браузер и реальную запись не трогаем — проверяем только чистую логику выбора.
"""

from __future__ import annotations

from dirizher.config import AudioSettings
from dirizher.container import MeetingSourceStore
from dirizher.bot.handlers.meeting import has_telemost_link, telemost_link


def test_audio_meeting_source_norm_defaults_telemost():
    assert AudioSettings().meeting_source_norm == "telemost"


def test_audio_meeting_source_norm_unknown_falls_back_to_loopback():
    assert AudioSettings(meeting_source="что-то").meeting_source_norm == "loopback"
    assert AudioSettings(meeting_source="LOOPBACK").meeting_source_norm == "loopback"
    assert AudioSettings(meeting_source="  Telemost ").meeting_source_norm == "telemost"


def test_source_store_default_and_override():
    store = MeetingSourceStore(default="telemost")
    assert store.get(1) == "telemost"  # дефолт
    assert store.set(1, "loopback") is True
    assert store.get(1) == "loopback"  # переопределили для чата
    assert store.get(2) == "telemost"  # другой чат — всё ещё дефолт


def test_source_store_rejects_garbage_and_normalizes_case():
    store = MeetingSourceStore(default="loopback")
    assert store.set(5, "TELEMOST") is True
    assert store.get(5) == "telemost"
    assert store.set(5, "zoom") is False
    assert store.get(5) == "telemost"  # мусор не перезаписал


def test_source_store_invalid_default_falls_back():
    assert MeetingSourceStore(default="nonsense").get(99) == "loopback"


def test_telemost_link_extracted():
    text = "Коллеги, созвон тут https://telemost.yandex.ru/j/12345678 в 15:00"
    assert has_telemost_link(text)
    assert telemost_link(text) == "https://telemost.yandex.ru/j/12345678"


def test_telemost_link_absent():
    assert not has_telemost_link("просто сообщение без ссылки")
    assert telemost_link("без ссылки") is None
