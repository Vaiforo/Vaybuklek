# Аудио и встречи

Этот документ описывает аудио-часть проекта «Дирижёр»: как голосовые сообщения и записи встреч превращаются в текст, саммари и задачи.

---

## Назначение

Аудио-пайплайн нужен для двух сценариев:

1. **Голосовые сообщения и видео-кружки в Telegram.** Бот распознаёт речь и передаёт текст в общий конвейер извлечения задач.
2. **Онлайн-встречи.** Система принимает аудиофайл или готовый транскрипт, формирует краткое саммари и выносит задачи на канбан-доску.

Если аудио выключено, бот не падает: используется mock-транскрайбер, который честно просит продублировать задачу текстом.

---

## Компоненты

| Компонент | Файл | Ответственность |
|---|---|---|
| `Transcriber` | `src/dirizher/audio/transcriber.py` | общий протокол распознавания речи |
| `MockTranscriber` | `src/dirizher/audio/transcriber.py` | безопасный fallback без внешних зависимостей |
| `AudioPipelineTranscriber` | `src/dirizher/audio/pipeline.py` | боевой пайплайн `noisereduce → pyannote → Whisper` |
| `SpeakerRegistry` | `src/dirizher/audio/speakers.py` | каркас реестра голосовых отпечатков |
| `MeetingService` | `src/dirizher/services/meeting.py` | транскрипт → саммари → задачи |
| `voice.py` | `src/dirizher/bot/handlers/voice.py` | обработка голосовых сообщений Telegram |
| `cli/meeting.py` | `src/dirizher/cli/meeting.py` | локальная обработка файла встречи |

---

## Поток обработки

```text
Аудио или transcript.txt
        │
        ▼
Transcriber
        │
        ├─ mock: безопасная деградация
        └─ audio enabled: noisereduce → pyannote → Whisper
        │
        ▼
TranscriptResult(text, segments, is_mock)
        │
        ▼
MeetingService / voice handler
        │
        ▼
TaskService.ingest(...)
        │
        ▼
подтверждение, дедупликация, YouGile, Telegram-ответ
```

---

## Настройка

Минимально аудио выключено и не требует дополнительных пакетов.

Для реального распознавания установите зависимости:

```bash
python -m pip install -e ".[audio]"
```

Затем включите аудио в `.env`:

```env
DIRIZHER_AUDIO__ENABLED=true
DIRIZHER_AUDIO__HF_TOKEN=              # токен Hugging Face для pyannote, если нужна диаризация
DIRIZHER_AUDIO__WHISPER_MODEL=small
DIRIZHER_AUDIO__DEVICE=cpu
```

---

## Обработка транскрипта встречи

```bash
python -m dirizher.cli.meeting path/to/transcript.txt
```

Формат файла:

```text
Анна: Нужно подготовить демо к пятнице.
Максим: Я доделаю авторизацию завтра.
```

Результат:

- краткое саммари встречи;
- список найденных задач;
- создание карточек на mock-доске или в YouGile, если интеграция настроена.

---

## Обработка аудиофайла

```bash
python -m dirizher.cli.meeting path/to/meeting.wav
```

Поддержка конкретных форматов зависит от установленных библиотек `soundfile`, `faster-whisper` и окружения. Для демо надёжнее использовать `.txt`-транскрипт.

---

## Ограничения

- Боевой захват системного звука Яндекс Телемоста не входит в текущий прототип.
- Диаризация через pyannote требует отдельный Hugging Face token и тяжёлые модели.
- Реестр голосовых отпечатков подготовлен как каркас; полноценное сопоставление голосов с участниками требует отдельной настройки embedding-инференса.
- На шумных записях качество распознавания зависит от микрофонов, языка, скорости речи и выбранной Whisper-модели.

---

## Проверка

```bash
python -m compileall -q src tests
python -m pytest -q
python -m dirizher.cli.meeting path/to/transcript.txt
```

При выключенном аудио основной бот и тестируемое ядро должны продолжать работать без тяжёлых аудио-зависимостей.
