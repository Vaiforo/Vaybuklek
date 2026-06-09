"""Конфигурация Дирижёра.

Вся конфигурация читается из переменных окружения / файла `.env`
(см. `.env.example`). В коде нет ни одного захардкоженного секрета.

Принцип «деградации в mock»: если ключ внешнего сервиса не задан,
соответствующий компонент переходит в mock-режим и система остаётся
полностью работоспособной офлайн (важно для демо и для CI).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Корень проекта (src/dirizher/config.py → ../../.. = репозиторий). Привязываем к
# нему папку данных, чтобы бот читал/писал ОДНИ И ТЕ ЖЕ файлы независимо от того,
# из какого каталога его запустили (иначе ./.data зависит от cwd — частый источник
# «пустого реестра/состояния», особенно при наличии двух копий проекта).
_ROOT = Path(__file__).resolve().parents[2]


def _data(name: str) -> str:
    return str(_ROOT / ".data" / name)


class TelegramSettings(BaseSettings):
    bot_token: str = ""
    mode: str = "polling"  # polling | webhook
    webhook_base_url: str = ""
    team_chat_id: int | None = None

    @field_validator("team_chat_id", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        # Пустое значение в .env (TEAM_CHAT_ID=) трактуем как «не задано»
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return v

    @property
    def is_mock(self) -> bool:
        return not self.bot_token


class LLMSettings(BaseSettings):
    provider: str = "mock"  # mock | groq | gigachat
    # >= confidence_threshold → задача; [ignore_threshold; confidence) → уточнить;
    # < ignore_threshold → молча игнорируем (чтобы бот не спамил на болтовню).
    confidence_threshold: float = 0.7
    ignore_threshold: float = 0.6

    groq_api_key: str = ""
    # Доп. ключи Groq через запятую — ротация при достижении дневного лимита (429).
    groq_api_keys: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    gigachat_credentials: str = ""
    gigachat_scope: str = "GIGACHAT_API_PERS"

    @property
    def groq_key_list(self) -> list[str]:
        """Все ключи Groq (основной + дополнительные), без пустых и дублей."""
        raw = [self.groq_api_key, *self.groq_api_keys.split(",")]
        keys: list[str] = []
        for k in raw:
            k = k.strip()
            if k and k not in keys:
                keys.append(k)
        return keys

    @property
    def effective_provider(self) -> str:
        """Провайдер с учётом наличия ключей — иначе откат в mock."""
        if self.provider == "groq" and self.groq_key_list:
            return "groq"
        if self.provider == "gigachat" and self.gigachat_credentials:
            return "gigachat"
        return "mock"


class YouGileSettings(BaseSettings):
    api_key: str = ""
    base_url: str = "https://ru.yougile.com/api-v2"
    column_todo: str = ""
    column_in_progress: str = ""
    column_done: str = ""

    @property
    def is_mock(self) -> bool:
        return not self.api_key


class MemorySettings(BaseSettings):
    # backend: lexical | chroma. По умолчанию lexical — без сетевых загрузок.
    # chroma включает семантический поиск (скачает embedding-модель при 1-м старте).
    backend: str = "lexical"
    chroma_path: str = _data("chroma")
    dedup_threshold: float = 0.83
    project_snapshot: str = _data("project.md")
    # JSON-хранилище состояния (команда + задачи) — переживает перезапуск бота.
    state_path: str = _data("state.json")
    # JSON-хранилище игровых профилей (XP/уровни/ачивки), п.10. В .gitignore.
    game_path: str = _data("gamification.json")


class AudioSettings(BaseSettings):
    enabled: bool = False
    # Бэкенд распознавания: groq (Whisper через Groq API, без локальных моделей)
    # или local (faster-whisper + опц. pyannote — требует тяжёлых зависимостей).
    backend: str = "local"  # groq | local
    # Модель локального faster-whisper. large-v3 — максимум точности (русский),
    # требует GPU/много RAM. Для слабого железа: small/medium/large-v3-turbo.
    whisper_model: str = "large-v3"
    # Мастер-переключатель устройства для ВСЕГО аудио-стека (Whisper + pyannote
    # диаризатор/эмбеддер): auto (CUDA если доступна, иначе CPU) | cuda | cpu.
    # При cuda без доступного GPU — безопасный откат на CPU с предупреждением.
    device: str = "auto"
    # compute_type для faster-whisper. Пусто → авто: на CUDA int8_float16
    # (оптимально для карт без tensor-ядер, напр. GTX 16xx), на CPU int8.
    # Варианты: float16, int8_float16, int8, float32.
    whisper_compute_type: str = ""
    # Тонкий оверрайд устройства ТОЛЬКО для Whisper. Пусто → берётся `device`.
    whisper_device: str = ""
    whisper_beam_size: int = 5  # ширина beam search (выше → точнее, но медленнее)
    # Доп. термины/жаргон проекта для initial_prompt Whisper (имена команды
    # подставляются автоматически из реестра голосов). Свободная фраза/перечисление.
    whisper_prompt: str = ""
    groq_whisper_model: str = "whisper-large-v3-turbo"  # модель Groq Whisper
    groq_api_key: str = ""
    # Доп. ключи Groq через запятую — ротация при достижении лимита (429).
    groq_api_keys: str = ""
    hf_token: str = ""  # диаризация pyannote + нейро-эмбеддинги (только backend=local)

    # ── Встречи: захват системного звука (loopback) ───────────────────────────
    meeting_samplerate: int = 16000
    meeting_silence_seconds: int = 180  # тишина дольше → авто-стоп записи встречи
    meeting_max_minutes: int = 180  # жёсткий предел длительности записи
    meeting_silence_rms: float = 0.004  # порог RMS, ниже которого чанк = тишина
    loopback_device: str = ""  # имя устройства вывода; пусто → колонки по умолчанию

    # ── Голосовые отпечатки (speaker embedding → авто-имя) ────────────────────
    # wespeaker-voxceleb-resnet34-LM — сильная нейромодель (ResNet34), её же
    # использует внутри diarization-3.1; заметно лучше разделяет голоса, чем
    # старая pyannote/embedding (SincNet). Нужен HF-токен (backend=local).
    # При смене модели прежние отпечатки изолируются (другая размерность) —
    # участников нужно перерегистрировать.
    embedding_model: str = "pyannote/wespeaker-voxceleb-resnet34-LM"
    voiceprints_path: str = _data("voiceprints.json")
    voiceprint_threshold: float = 0.72
    # Порог авто-именования спикеров НА ВСТРЕЧАХ; ниже него — анонимный Speaker_N.
    # Откалибровано под нейроэмбеддинги wespeaker: реальные участники на loopback
    # дают cos ~0.47–0.60, разные люди — заметно ниже, поэтому 0.45 безопасно
    # отделяет своих от чужих. /who (дообучение из встречи) ещё повышает запас.
    # (Старое 0.82 годилось для иной шкалы и резало верные опознания.)
    voiceprint_name_threshold: float = 0.45
    # Косинус-близость, выше которой двух «спикеров» pyannote считаем одним
    # человеком и схлопываем (лечит пере-сегментацию: 6 меток на 3 человек).
    # Ниже → агрессивнее объединяет (риск склеить разных), выше → меньше слияний.
    speaker_merge_similarity: float = 0.70
    # Косинус-близость, выше которой двух «спикеров» pyannote считаем одним
    # человеком и схлопываем (лечит пере-сегментацию на loopback). Понизь
    # (напр. 0.70), если один человек дробится на несколько Speaker_N; повысь,
    # если разных людей склеивает в одного.
    voiceprint_merge_threshold: float = 0.80

    @property
    def groq_key_list(self) -> list[str]:
        """Ключи Groq для Whisper (основной + дополнительные), без пустых и дублей."""
        raw = [self.groq_api_key, *self.groq_api_keys.split(",")]
        keys: list[str] = []
        for k in raw:
            k = k.strip()
            if k and k not in keys:
                keys.append(k)
        return keys

    @property
    def is_mock(self) -> bool:
        return not self.enabled


class ScheduleSettings(BaseSettings):
    morning_digest_cron: str = "0 9 * * *"        # утренняя сводка задач на день
    reminder_cron: str = "0 10,15 * * *"
    evening_reconcile_cron: str = "0 20 * * *"
    leaderboard_cron: str = "0 18 * * 5"          # лидерборд по пятницам в 18:00
    remind_before_hours: int = 24


class APISettings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8080
    shared_secret: str = ""


class Settings(BaseSettings):
    """Корневые настройки. Вложенные секции через двойное подчёркивание:
    например `DIRIZHER_TELEGRAM__BOT_TOKEN`.
    """

    model_config = SettingsConfigDict(
        env_prefix="DIRIZHER_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    timezone: str = "Europe/Moscow"
    log_level: str = "INFO"

    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    yougile: YouGileSettings = Field(default_factory=YouGileSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    schedule: ScheduleSettings = Field(default_factory=ScheduleSettings)
    api: APISettings = Field(default_factory=APISettings)

    def mode_banner(self) -> str:
        """Однострочный отчёт, какие компоненты боевые, а какие в mock."""
        flag = lambda mock: "mock" if mock else "live"  # noqa: E731
        return (
            f"telegram={flag(self.telegram.is_mock)} "
            f"llm={self.llm.effective_provider} "
            f"yougile={flag(self.yougile.is_mock)} "
            f"audio={flag(self.audio.is_mock)}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
