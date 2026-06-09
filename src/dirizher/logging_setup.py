"""Единая настройка логирования."""

from __future__ import annotations

import logging
from pathlib import Path

_CONFIGURED = False

# Файл лога рядом с данными (репо/.data/dirizher.log) — чтобы диагностику
# (диаризация/именование спикеров) можно было поднять постфактум, а не только
# из живой консоли.
_LOG_FILE = Path(__file__).resolve().parents[2] / ".data" / "dirizher.log"


def setup_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(_LOG_FILE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass  # файл лога необязателен — консоли достаточно
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s │ %(levelname)-7s │ %(name)-22s │ %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )
    # aiogram/httpx слишком болтливы на INFO
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
