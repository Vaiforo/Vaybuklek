"""Приём «сырого» аудио из внешних источников (браузерное расширение) и
приведение его к формату, который ждёт конвейер встреч: WAV 16kHz моно PCM.

Расширение шлёт `audio/webm;codecs=opus` (родной выход MediaRecorder). Конвейер
встреч (loopback) даёт именно WAV 16k моно, и диаризация pyannote/torchaudio
ждёт PCM. Поэтому перекодируем входной блоб в такой же WAV.

Стратегия перекодировки (с аккуратным fallback, чтобы не падать на разных
машинах):
1. Если файл уже WAV 16k моно — отдаём его как есть.
2. ffmpeg в PATH — самый надёжный путь.
3. pyav (`av`, тянется как зависимость faster-whisper) — декодируем без внешних
   бинарников.
4. Если ничего не вышло — возвращаем исходный путь и логируем: faster-whisper и
   Groq обычно сами декодируют opus, расшифровка не сорвётся (пострадает только
   локальная диаризация).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

from ..logging_setup import get_logger

log = get_logger("dirizher.audio.ingest")

_TARGET_RATE = 16000
_TARGET_CHANNELS = 1


def _is_wav16k_mono(path: str) -> bool:
    """Файл уже WAV 16kHz моно? Тогда перекодировка не нужна."""
    try:
        with wave.open(path, "rb") as w:
            return (
                w.getnchannels() == _TARGET_CHANNELS
                and w.getframerate() == _TARGET_RATE
                and w.getsampwidth() == 2
            )
    except Exception:  # noqa: BLE001
        return False


def _tmp_wav() -> str:
    with tempfile.NamedTemporaryFile(prefix="dirizher_ingest_", suffix=".wav", delete=False) as tmp:
        return tmp.name


def _via_ffmpeg(src: str, dst: str) -> bool:
    exe = shutil.which("ffmpeg")
    if not exe:
        return False
    try:
        subprocess.run(
            [exe, "-y", "-i", src, "-ac", str(_TARGET_CHANNELS), "-ar", str(_TARGET_RATE),
             "-sample_fmt", "s16", dst],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return Path(dst).stat().st_size > 44  # больше пустого WAV-заголовка
    except Exception:  # noqa: BLE001
        log.warning("ffmpeg-перекодировка не удалась", exc_info=True)
        return False


def _via_pyav(src: str, dst: str) -> bool:
    """Декодировать любой контейнер в WAV 16k моно через pyav + ресемплер."""
    try:
        import av  # type: ignore
    except Exception:  # noqa: BLE001
        return False
    try:
        resampler = av.audio.resampler.AudioResampler(
            format="s16", layout="mono", rate=_TARGET_RATE
        )
        with av.open(src) as container, wave.open(dst, "wb") as out:
            out.setnchannels(_TARGET_CHANNELS)
            out.setsampwidth(2)
            out.setframerate(_TARGET_RATE)
            stream = container.streams.audio[0]
            for frame in container.decode(stream):
                for rs in resampler.resample(frame):
                    out.writeframes(bytes(rs.planes[0]))
            # Слив остатка из ресемплера (pyav >= 9 буферизует кадры).
            for rs in resampler.resample(None):
                out.writeframes(bytes(rs.planes[0]))
        return Path(dst).stat().st_size > 44
    except Exception:  # noqa: BLE001
        log.warning("pyav-перекодировка не удалась", exc_info=True)
        return False


def to_wav16k_mono(src_path: str) -> str:
    """Привести аудио к WAV 16kHz моно. Вернуть путь к готовому файлу.

    Если файл уже в нужном формате — возвращается исходный путь. Если ни один
    перекодировщик недоступен — тоже возвращается исходный путь (транскрайбер
    обычно декодирует opus сам), о чём пишем в лог.
    """
    if _is_wav16k_mono(src_path):
        return src_path

    dst = _tmp_wav()
    if _via_ffmpeg(src_path, dst) or _via_pyav(src_path, dst):
        return dst

    # Перекодировка недоступна — убираем пустышку и отдаём как есть.
    try:
        Path(dst).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    log.warning(
        "Не нашёл ffmpeg/pyav — отдаю аудио транскрайберу как есть (%s). "
        "Локальная диаризация может пострадать; расшифровка обычно ок.",
        src_path,
    )
    return src_path
