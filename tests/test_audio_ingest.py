"""Перекодировка входного аудио в WAV 16kHz моно (audio/ingest.py)."""

from __future__ import annotations

import wave
from pathlib import Path

from dirizher.audio.ingest import _is_wav16k_mono, to_wav16k_mono


def _write_wav(path: str, rate: int, channels: int) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * rate)  # 1 секунда тишины


def test_already_wav16k_mono_returned_as_is(tmp_path):
    src = str(tmp_path / "ok.wav")
    _write_wav(src, 16000, 1)
    assert _is_wav16k_mono(src)
    assert to_wav16k_mono(src) == src  # перекодировка не нужна


def test_wrong_format_is_not_recognized(tmp_path):
    stereo = str(tmp_path / "stereo.wav")
    _write_wav(stereo, 44100, 2)
    assert not _is_wav16k_mono(stereo)


def test_unsupported_input_falls_back_to_source(tmp_path):
    """Не-аудио мусор: без ffmpeg/pyav возвращаем исходный путь, не падаем."""
    src = str(tmp_path / "junk.webm")
    Path(src).write_bytes(b"not really audio")
    out = to_wav16k_mono(src)
    assert isinstance(out, str)
    assert Path(out).exists()
