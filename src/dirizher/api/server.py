"""HTTP-API для оркестрации n8n.

Роль n8n (как в отчёте): принимать Telegram-webhook и cron-триггеры и
маршрутизировать их в ядро. Само ядро — это сервисы Дирижёра; API лишь
даёт n8n стабильные точки входа:

  POST /ingest/telegram          — n8n форвардит апдейт Telegram сюда
  POST /jobs/reminders           — cron: проверка дедлайнов и напоминания
  POST /jobs/evening-reconcile   — cron: вечерняя сверка отчётов
  POST /meeting/audio            — приём аудио созвона из браузерного расширения
  GET  /meeting/command          — расширение опрашивает: писать или стоять
  GET  /meeting/audio/health     — пинг для расширения (проверка связи/токена)
  GET  /health                   — статус и режимы компонентов

Защита: общий секрет в заголовке X-Dirizher-Token (если задан в .env).
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile

from ..container import AppContainer
from ..logging_setup import get_logger
from ..scheduler.jobs import (
    run_evening_reconciliation,
    run_leaderboard_post,
    run_morning_digest,
    run_reminders,
    run_trash_purge,
)

log = get_logger("dirizher.api")


def create_api(container: AppContainer) -> FastAPI:
    app = FastAPI(title="Дирижёр API", version="0.1.0")
    secret = container.settings.api.shared_secret

    def _auth(token: str | None) -> None:
        if secret and token != secret:
            raise HTTPException(status_code=401, detail="invalid token")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "modes": container.settings.mode_banner(),
            "open_tasks": len(container.repo.open()),
            "memory_backend": container.memory.backend_name,
        }

    @app.post("/ingest/telegram")
    async def ingest_telegram(
        update: dict, x_dirizher_token: str | None = Header(default=None)
    ) -> dict[str, str]:
        _auth(x_dirizher_token)
        dp = getattr(container, "dp", None)
        if dp is None or container.bot is None:
            raise HTTPException(status_code=503, detail="bot not running")
        from aiogram.types import Update

        await dp.feed_update(container.bot, Update.model_validate(update))
        return {"status": "processed"}

    @app.post("/jobs/morning-digest")
    async def jobs_morning(x_dirizher_token: str | None = Header(default=None)) -> dict[str, int]:
        _auth(x_dirizher_token)
        chats = await run_morning_digest(container)
        return {"chats_notified": chats}

    @app.post("/jobs/reminders")
    async def jobs_reminders(x_dirizher_token: str | None = Header(default=None)) -> dict[str, int]:
        _auth(x_dirizher_token)
        sent = await run_reminders(container)
        return {"reminders_sent": sent}

    @app.post("/jobs/leaderboard")
    async def jobs_leaderboard(x_dirizher_token: str | None = Header(default=None)) -> dict[str, int]:
        _auth(x_dirizher_token)
        chats = await run_leaderboard_post(container)
        return {"chats_notified": chats}

    @app.post("/jobs/trash-purge")
    async def jobs_trash_purge(x_dirizher_token: str | None = Header(default=None)) -> dict[str, int]:
        _auth(x_dirizher_token)
        removed = await run_trash_purge(container)
        return {"tasks_deleted": removed}

    @app.post("/jobs/evening-reconcile")
    async def jobs_evening(x_dirizher_token: str | None = Header(default=None)) -> dict[str, int]:
        _auth(x_dirizher_token)
        chats = await run_evening_reconciliation(container)
        return {"chats_notified": chats}

    @app.get("/meeting/audio/health")
    async def meeting_audio_health(
        x_dirizher_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Пинг для браузерного расширения: проверить связь и токен."""
        _auth(x_dirizher_token)
        return {
            "status": "ok",
            "bot_running": container.bot is not None,
            "transcriber": container.transcriber.name,
        }

    @app.get("/meeting/command")
    async def meeting_command(
        chat_id: int,
        x_dirizher_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Расширение опрашивает это раз в несколько секунд и реконсилирует:

        - `desired == "recording"` и сейчас не пишет → стартовать захват вкладки;
        - `desired == "idle"` и сейчас пишет → остановить и отправить запись.

        Заодно отдаём пороги авто-стопа по тишине, чтобы клиент не хардкодил их.
        """
        _auth(x_dirizher_token)
        audio = container.settings.audio
        return {
            "desired": container.extension_signal.desired(chat_id),
            "silence_seconds": audio.meeting_silence_seconds,
            "silence_rms": audio.meeting_silence_rms,
        }

    @app.post("/meeting/audio")
    async def meeting_audio(
        file: UploadFile = File(...),
        chat_id: int = Form(...),
        reason: str = Form("extension"),
        x_dirizher_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Принять аудио созвона из браузерного расширения и прогнать его через
        тот же конвейер, что и запись встречи (recorder → _process_recording).

        Файл сохраняется во временный, перекодируется в WAV 16k моно и уходит в
        обработку. HTTP-ответ не ждёт расшифровку: запускаем фоновой задачей.
        """
        _auth(x_dirizher_token)
        if container.bot is None:
            raise HTTPException(status_code=503, detail="bot not running")

        from ..audio.ingest import to_wav16k_mono
        from ..bot.handlers.meeting import _process_recording

        # Запись пришла — гасим желаемое состояние, чтобы расширение не стартовало
        # автозапись заново на следующем опросе.
        container.extension_signal.want_stop(chat_id)

        data = await file.read()
        suffix = Path(file.filename or "audio.webm").suffix or ".webm"
        with tempfile.NamedTemporaryFile(prefix="dirizher_ext_", suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            raw_path = tmp.name

        # Перекодировку (CPU-bound) и обработку уводим в фон, чтобы быстро
        # ответить расширению. Исключения логируем — не теряем.
        async def _run() -> None:
            wav_path = raw_path
            try:
                wav_path = await asyncio.to_thread(to_wav16k_mono, raw_path)
                await _process_recording(container, container.bot, chat_id, wav_path, reason)
            except Exception:  # noqa: BLE001
                log.exception("Сбой обработки аудио из расширения (chat_id=%s)", chat_id)
            finally:
                # Если перекодировали в отдельный файл — исходник больше не нужен.
                # _process_recording сам удаляет переданный путь (wav_path).
                if wav_path != raw_path:
                    try:
                        Path(raw_path).unlink(missing_ok=True)
                    except Exception:  # noqa: BLE001
                        pass

        asyncio.create_task(_run())
        return {"status": "processing", "bytes": len(data)}

    return app
