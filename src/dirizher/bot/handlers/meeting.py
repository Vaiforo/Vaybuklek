"""Встречи: запись системного звука по ссылке Телемоста + регистрация голоса.

Поток (всё автоматизировано):
1. Кто-то кидает в чат ссылку telemost.yandex.ru/... → бот сам начинает писать
   СИСТЕМНЫЙ звук машины (loopback). Бот не «входит» в конфу — слушает звук ПК,
   который уже в звонке (захват с драйвера, как в кейсе).
2. Запись сама останавливается по долгой тишине или по `/meeting_stop`.
3. Запись → Whisper + диаризация + авто-имена по голосу → саммари + задачи на доску.

`/enroll_voice` — записать голосовой отпечаток участника, чтобы на встречах
Speaker_1 заменялся реальным именем.
`/who Speaker_1 Имя` — дообучить голос неопознанного спикера ПРЯМО ИЗ записи
последней встречи (реальные loopback-условия) — точнее, чем чистый микрофон.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from html import escape as esc
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from ...container import AppContainer
from ...logging_setup import get_logger
from ...domain.models import TeamMember
from ...permissions import can_start_meeting
from ..flow import present
from ..states import EnrollVoice

router = Router(name="meeting")
log = get_logger("dirizher.bot.meeting")

# Ссылки Яндекс.Телемоста (и telemost.yandex.* доменов)
_TELEMOST_RE = re.compile(r"https?://telemost\.yandex\.[a-z]+/\S+", re.IGNORECASE)


def _actor(message: Message, c: AppContainer) -> TeamMember | None:
    user = message.from_user
    if user is None:
        return None
    return c.team.register(TeamMember(user_id=user.id, username=user.username, full_name=user.full_name))


def has_telemost_link(text: str) -> bool:
    return bool(_TELEMOST_RE.search(text or ""))


def telemost_link(text: str) -> str | None:
    m = _TELEMOST_RE.search(text or "")
    return m.group(0) if m else None


def _diarize_segments(c: AppContainer, path: str, segments: list) -> None:
    """Синхронно разметить говорящих: сначала pyannote (если есть HF-токен),

    иначе — MFCC-кластеризация со строгим порогом именования. Выполняется в
    отдельном потоке (модели тяжёлые/синхронные).
    """
    from ...audio.diarize import assign_speakers_by_voice, assign_speakers_pyannote

    audio = c.settings.audio
    if assign_speakers_pyannote(
        path,
        segments,
        audio.hf_token,
        embedder=c.embedder,
        registry=c.speakers,
        name_threshold=audio.voiceprint_name_threshold,
        merge_threshold=audio.speaker_merge_similarity,
        device=audio.device,
    ):
        return
    assign_speakers_by_voice(
        path,
        segments,
        c.embedder,
        c.speakers,
        name_threshold=audio.voiceprint_name_threshold,
    )


async def _diarize_meeting(c: AppContainer, path: str, transcript) -> None:
    """Проставить говорящих сегментам встречи.

    Запускаем, только если транскрайбер сам не разделил спикеров (облачный путь):
    если в сегментах уже больше одного говорящего — значит локальный pyannote их
    разметил, не трогаем.
    """
    if not getattr(transcript, "segments", None):
        return
    if c.embedder is None and not c.settings.audio.hf_token:
        return
    if len({s.speaker for s in transcript.segments}) > 1:
        return
    try:
        await asyncio.to_thread(_diarize_segments, c, path, transcript.segments)
    except Exception:  # noqa: BLE001
        log.exception("Диаризация встречи пропущена")


def _discard_file(path: str | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def _remember_meeting(c: AppContainer, chat_id: int, path: str, segments: list) -> None:
    """Запомнить запись чата для дообучения голосов; прежнюю запись удалить."""
    prev = c.recent_meetings.get(chat_id)
    if prev and prev.get("path") != path:
        _discard_file(prev.get("path"))
    c.recent_meetings[chat_id] = {"path": path, "segments": list(segments)}


def _unnamed_labels(segments: list) -> list[str]:
    """Метки неопознанных спикеров (Speaker_N) в порядке появления."""
    seen: list[str] = []
    for s in segments:
        spk = str(getattr(s, "speaker", ""))
        if spk.startswith("Speaker_") and spk not in seen:
            seen.append(spk)
    return seen


async def _suggest_learning(c: AppContainer, bot, chat_id: int, segments: list) -> None:
    """Подсказать /who, если в записи остались неопознанные спикеры."""
    if c.embedder is None:
        return
    labels = _unnamed_labels(segments)
    if not labels:
        return
    example = labels[0]
    await bot.send_message(
        chat_id,
        "🎓 Кого-то отметил как "
        + ", ".join(f"<b>{esc(label)}</b>" for label in labels)
        + ". Подпишите голос — запомню его из этой записи (loopback-условия):\n"
        + f"<code>/who {example} Имя</code>",
    )


async def _process_recording(c: AppContainer, bot, chat_id: int, path: str | None, reason: str) -> None:
    """Колбэк по завершении записи: распознать, выделить задачи, отчитаться."""
    c.active_meetings.pop(chat_id, None)
    why = {"silence": "тишина", "timeout": "лимит времени", "manual": "по команде"}.get(reason, reason)
    if not path:
        await bot.send_message(chat_id, f"⏹️ Запись встречи остановлена ({why}) — звука не было.")
        return
    await bot.send_message(chat_id, f"⏹️ Запись остановлена ({why}). Распознаю встречу…")
    try:
        transcript = await c.transcriber.transcribe(path)
        # Разделяем реплики по голосу прямо на записи встречи (пока файл жив).
        # Облачный Groq отдаёт сегменты без говорящих — кластеризуем их сами;
        # делаем это только для встреч, личные ГС остаются без меток спикеров.
        await _diarize_meeting(c, path, transcript)
    except Exception as e:  # noqa: BLE001
        log.exception("Сбой распознавания встречи")
        await bot.send_message(chat_id, f"Не смог распознать встречу 😕\n<code>{esc(str(e))}</code>")
        _discard_file(path)
        return

    if transcript.is_mock or not transcript.text.strip():
        await bot.send_message(chat_id, "Речь на встрече не распознана.")
        _discard_file(path)
        return

    result = await c.meeting.process(transcript, chat_id=chat_id)
    await bot.send_message(chat_id, "📝 <b>Саммари встречи</b>\n" + esc(result.summary))

    # Запись + сегменты держим, чтобы командой /who дообучить голос неопознанного
    # спикера из реальных loopback-условий (см. cmd_who). Предыдущую запись чата
    # удаляем. Подсказываем, как подписать, если остались Speaker_N.
    _remember_meeting(c, chat_id, path, transcript.segments)
    await _suggest_learning(c, bot, chat_id, transcript.segments)

    if not result.processed:
        await bot.send_message(chat_id, "Задач из встречи не выделил.")
        return
    # Те же кнопки подтверждения/правки, что и для задач из чата: в ручном режиме
    # каждую задачу можно подтвердить, поправить (срок/исполнителя) или отклонить;
    # в авто-режиме (/mode auto) — заводятся сразу.
    await bot.send_message(
        chat_id, f"📋 Нашёл задач из встречи: <b>{len(result.processed)}</b> — проверьте:"
    )
    await present(bot, c, result.processed, chat_id)


@router.message(F.text.func(lambda t: has_telemost_link(t)))
async def on_telemost_link(message: Message, c: AppContainer) -> None:
    chat_id = message.chat.id
    actor = _actor(message, c)
    if not can_start_meeting(actor):
        await message.answer("⛔ Созвоны могут начинать только руководители и суперюзеры.")
        return
    if c.transcriber.name == "mock":
        await message.answer(
            "🔗 Вижу ссылку на встречу, но распознавание выключено "
            "(<code>DIRIZHER_AUDIO__ENABLED=false</code>) — запись не веду."
        )
        return
    if chat_id in c.active_meetings:
        await message.answer("🔴 Уже пишу эту встречу. Остановить — /meeting_stop.")
        return

    from ...audio.recorder import MeetingRecorder, TelemostRecorder, loopback_available

    if not loopback_available():
        await message.answer(
            "🎧 Чтобы писать встречу, нужен захват системного звука. Установите:\n"
            "<code>pip install soundcard</code> и перезапустите бота."
        )
        return

    loop = asyncio.get_running_loop()
    bot = message.bot
    audio = c.settings.audio

    async def on_finish(path: str | None, reason: str) -> None:
        await _process_recording(c, bot, chat_id, path, reason)

    source = c.meeting_source.get(chat_id)
    rec: MeetingRecorder
    via = "системный звук"
    if source == "telemost":
        from ...audio.telemost import playwright_available

        link = telemost_link(message.text) or ""
        if not playwright_available():
            await message.answer(
                "🌐 Источник <b>Телемост</b> требует браузер Playwright "
                "(<code>pip install playwright</code> + <code>playwright install chromium</code>).\n"
                "Пока пишу <b>системный звук</b> — подключитесь к звонку на этой машине."
            )
            rec = MeetingRecorder(audio, on_finish, loop)
        else:
            await message.answer(
                "🌐 Захожу в звонок Телемоста как <b>" + esc(audio.telemost_join_name) + "</b>…"
            )
            rec = TelemostRecorder(link, audio, on_finish, loop)
            via = "Телемост (бот в звонке)"
    else:
        rec = MeetingRecorder(audio, on_finish, loop)

    if not rec.start():
        # Если бот не смог войти в Телемост — мягко откатываемся на системный звук.
        if isinstance(rec, TelemostRecorder):
            await message.answer(
                "⚠️ Не удалось войти в звонок автоматически. "
                "Пишу <b>системный звук</b> — подключитесь к встрече на этой машине вручную."
            )
            rec = MeetingRecorder(audio, on_finish, loop)
            via = "системный звук"
            if not rec.start():
                await message.answer("Не нашёл устройство для захвата звука 😕 Проверьте колонки/драйвер.")
                return
        else:
            await message.answer("Не нашёл устройство для захвата звука 😕 Проверьте колонки/драйвер.")
            return
    c.active_meetings[chat_id] = rec
    mins = c.settings.audio.meeting_silence_seconds // 60
    await message.answer(
        f"🔴 <b>Пишу встречу</b> ({via}).\n"
        f"Остановлю сам после тишины (~{mins} мин) или командой /meeting_stop.\n"
        "Когда закончу — пришлю саммари и вынесу задачи на доску."
    )


_SOURCE_LABEL = {
    "telemost": "🌐 Подключение к Телемосту (бот сам входит в звонок)",
    "loopback": "🎧 Запись системного звука (машина уже в звонке)",
}


@router.message(Command("meeting_source"))
async def cmd_meeting_source(message: Message, c: AppContainer) -> None:
    """`/meeting_source [telemost|loopback]` — показать/сменить источник звука встреч."""
    parts = (message.text or "").split(maxsplit=1)
    chat_id = message.chat.id
    if len(parts) < 2 or not parts[1].strip():
        current = c.meeting_source.get(chat_id)
        await message.answer(
            "🎙️ <b>Источник звука встреч</b>\n"
            f"Сейчас: {_SOURCE_LABEL[current]}\n\n"
            "Сменить:\n"
            "• <code>/meeting_source telemost</code> — бот заходит в звонок по ссылке\n"
            "• <code>/meeting_source loopback</code> — пишем системный звук машины"
        )
        return
    value = parts[1].strip().lower()
    if not c.meeting_source.set(chat_id, value):
        await message.answer("Не понял источник. Варианты: <code>telemost</code> или <code>loopback</code>.")
        return
    await message.answer(f"✅ Источник звука встреч: {_SOURCE_LABEL[c.meeting_source.get(chat_id)]}")


@router.message(Command("meeting_stop"))
async def cmd_meeting_stop(message: Message, c: AppContainer) -> None:
    rec = c.active_meetings.get(message.chat.id)
    if rec is None:
        await message.answer("Сейчас запись встречи не идёт.")
        return
    rec.stop("manual")  # обработку и ответ даст колбэк on_finish
    await message.answer("⏹️ Останавливаю запись, обрабатываю…")


# ── Регистрация голосового отпечатка ─────────────────────────────────────────
@router.message(Command("enroll_voice"))
async def cmd_enroll_voice(message: Message, c: AppContainer, state: FSMContext) -> None:
    if c.embedder is None:
        await message.answer(
            "🎙️ Авто-имена по голосу доступны при включённом распознавании:\n"
            "<code>DIRIZHER_AUDIO__ENABLED=true</code> в <code>.env</code>."
        )
        return
    await state.set_state(EnrollVoice.waiting_voice)
    await message.answer("🎙️ Пришлите короткое голосовое (5–10 сек) — запомню ваш голос для встреч.")


@router.message(EnrollVoice.waiting_voice, F.voice | F.audio)
async def on_enroll_voice(message: Message, c: AppContainer, state: FSMContext) -> None:
    await state.clear()
    user = message.from_user
    member = c.team.resolve(user.username or user.full_name) if user else None
    name = (member.full_name if member else None) or (user.full_name if user else "Участник")

    media = message.voice or message.audio
    suffix = ".oga" if message.voice else ".ogg"
    with tempfile.NamedTemporaryFile(prefix="dirizher_enroll_", suffix=suffix, delete=False) as tmp:
        path = tmp.name
    try:
        file = await message.bot.get_file(media.file_id)
        await message.bot.download_file(file.file_path, destination=path)
        emb = await asyncio.to_thread(c.embedder.embed_file, path)
        count = c.speakers.enroll(name, emb)
        hint = (
            "\n💡 Голос на созвоне звучит иначе, чем в этой записи (кодек связи). "
            "Чтобы узнавал точнее — пришлите <code>/enroll_voice</code> ещё раз, "
            "лучше прямо во время/после созвона."
            if count < 2
            else ""
        )
        await message.answer(
            f"✅ Запомнил голос: <b>{esc(name)}</b> (образцов: {count}). "
            f"На встречах подпишу ваши реплики.{hint}"
        )
    except Exception as e:  # noqa: BLE001
        log.exception("Сбой регистрации голоса")
        await message.answer(f"Не смог запомнить голос 😕\n<code>{esc(str(e))}</code>")
    finally:
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


def _learn_voice_from_meeting(c: AppContainer, meeting: dict, label: str, name: str) -> int:
    """Извлечь голос спикера `label` из записи встречи и зарегистрировать на `name`.

    Берём все реплики этого спикера (с таймкодами) и усредняем их эмбеддинг через
    embed_turns — отпечаток в РЕАЛЬНЫХ loopback-условиях созвона, что закрывает
    разрыв с чистым микрофоном при /enroll_voice. Возвращает число образцов имени;
    -1 — если у спикера нет пригодных реплик. Синхронно (модели тяжёлые).
    """
    segs = [
        s for s in meeting["segments"]
        if str(getattr(s, "speaker", "")) == label and s.start is not None and s.end is not None
    ]
    if not segs:
        return -1
    turns = [(s.start, s.end, label) for s in segs]
    embs = c.embedder.embed_turns(meeting["path"], turns)
    vec = embs.get(label)
    if not vec:
        return -1
    return c.speakers.enroll(name, vec)


@router.message(Command("who"))
async def cmd_who(message: Message, c: AppContainer) -> None:
    """`/who Speaker_1 Имя` — запомнить голос спикера из последней записи встречи."""
    if c.embedder is None:
        await message.answer(
            "🎙️ Дообучение голоса доступно при включённом распознавании "
            "(<code>DIRIZHER_AUDIO__ENABLED=true</code>)."
        )
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3 or not parts[2].strip():
        await message.answer(
            "Формат: <code>/who Speaker_1 Имя</code> — запомню голос этого спикера "
            "из последней встречи (метки видны в саммари)."
        )
        return
    label, raw_name = parts[1].strip(), parts[2].strip()
    meeting = c.recent_meetings.get(message.chat.id)
    if not meeting:
        await message.answer("Нет недавней записи встречи для обучения. Запишите встречу и повторите.")
        return
    # Имя приводим к канону команды (как в /enroll_voice): @username/алиас → ФИО.
    member = c.team.resolve(raw_name.lstrip("@"))
    name = (member.full_name if member else None) or raw_name
    try:
        count = await asyncio.to_thread(_learn_voice_from_meeting, c, meeting, label, name)
    except Exception as e:  # noqa: BLE001
        log.exception("Сбой дообучения голоса из встречи")
        await message.answer(f"Не смог запомнить голос 😕\n<code>{esc(str(e))}</code>")
        return
    if count < 0:
        await message.answer(
            f"В последней встрече нет пригодных реплик «{esc(label)}» "
            "(слишком короткие или метки не совпали). Проверьте метку в саммари."
        )
        return
    await message.answer(
        f"✅ Запомнил голос <b>{esc(name)}</b> из записи встречи "
        f"(образцов: {count}). Теперь буду узнавать его на созвонах."
    )
