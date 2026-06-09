"""Авто-имена спикеров: Speaker_1 → @username по голосовому отпечатку.

Логику опознавания (реестр, per-segment identification, разметка встречи)
проверяем на детерминированном фейковом эмбеддере — без аудио-библиотек.
Отдельный тест на реальном SignalEmbedder проверяет, что разные голоса
действительно расходятся по косинусу.
"""

from __future__ import annotations

import math
import random
import wave

import pytest

from dirizher.audio.embeddings import SignalEmbedder, build_embedder
from dirizher.audio.pipeline import WhisperPipeline, _TimedSegment
from dirizher.audio.speakers import SpeakerRegistry
from dirizher.config import AudioSettings
from dirizher.domain.models import TeamMember
from dirizher.repository import TeamRegistry
from dirizher.services.meeting import MeetingService
from dirizher.audio.transcriber import Segment, TranscriptResult


# ── фейковый эмбеддер: голос задаётся явным вектором по индексу сегмента ──────
class FakeEmbedder:
    name = "fake"

    def __init__(self, per_index: dict[str, list[float]]) -> None:
        self._per_index = per_index  # {"0": vec, "1": vec, ...}

    def embed_file(self, wav_path: str) -> list[float]:  # pragma: no cover - не нужен тут
        return self._per_index.get("file", [1.0, 0.0])

    def embed_turns(self, wav_path, turns):
        out = {}
        for _s, _e, label in turns:
            if label in self._per_index:
                out[label] = self._per_index[label]
        return out


def _reg(tmp_path, **voices):
    r = SpeakerRegistry(path=str(tmp_path / "vp.json"), threshold=0.8)
    for name, vec in voices.items():
        r.enroll(name, vec)
    return r


def test_registry_enroll_identify_and_reject(tmp_path):
    r = _reg(tmp_path, Andrey=[1.0, 0.0, 0.0], Stef=[0.0, 1.0, 0.0])
    assert r.identify([0.99, 0.1, 0.0]) == "Andrey"
    assert r.identify([0.1, 0.99, 0.0]) == "Stef"
    assert r.identify([0.0, 0.0, 1.0]) is None  # ортогонален обоим → не угадываем
    assert len(r) == 2 and set(r.names()) == {"Andrey", "Stef"}


def test_registry_persist_roundtrip(tmp_path):
    p = str(tmp_path / "vp.json")
    SpeakerRegistry(path=p).enroll("Стеф", [0.3, 0.7])
    again = SpeakerRegistry(path=p)
    assert again.names() == ["Стеф"]
    assert again.identify([0.3, 0.7]) == "Стеф"


def test_enroll_replaces_same_name(tmp_path):
    r = SpeakerRegistry(path=str(tmp_path / "vp.json"))
    r.enroll("Стеф", [1.0, 0.0])
    r.enroll("Стеф", [0.0, 1.0])  # перезапись, не дубль
    assert len(r) == 1
    assert r.identify([0.0, 1.0]) == "Стеф"


def test_pipeline_identifies_two_speakers(tmp_path):
    """Двое реально разных говорящих → кластеризуются и размечаются раздельно."""
    reg = _reg(tmp_path, Andrey=[1.0, 0.0], Stef=[0.0, 1.0])
    emb = FakeEmbedder({"0": [0.99, 0.02], "1": [0.02, 0.99], "2": [0.97, 0.05], "3": [0.04, 0.98]})
    pipe = WhisperPipeline(AudioSettings(enabled=True), speaker_registry=reg, embedder=emb)
    assert pipe._can_identify() is True
    segs = [_TimedSegment(i * 2.0, i * 2.0 + 2.0, f"реплика {i}", "Speaker_1") for i in range(4)]
    pipe._identify_segments("ignored.wav", segs)
    assert [s.speaker for s in segs] == ["Andrey", "Stef", "Andrey", "Stef"]


def test_pipeline_does_not_split_single_speaker(tmp_path):
    """Говорил один человек (сегменты взаимно похожи) → один кластер → одно имя.

    Даже несмотря на двух зарегистрированных, монолог не дробится.
    """
    reg = _reg(tmp_path, Andrey=[1.0, 0.0], Stef=[0.0, 1.0])
    emb = FakeEmbedder({"0": [0.99, 0.02], "1": [0.97, 0.05], "2": [0.98, 0.03], "3": [0.99, 0.04]})
    pipe = WhisperPipeline(AudioSettings(enabled=True), speaker_registry=reg, embedder=emb)
    segs = [_TimedSegment(i * 2.0, i * 2.0 + 2.0, f"моя реплика {i}", "Speaker_1") for i in range(4)]
    pipe._identify_segments("x.wav", segs)
    assert [s.speaker for s in segs] == ["Andrey", "Andrey", "Andrey", "Andrey"]


def test_pipeline_oversplit_same_voice_gets_one_name(tmp_path):
    """Даже если один голос распался на два кластера — оба назовутся одним именем."""
    reg = _reg(tmp_path, Andrey=[1.0, 0.0], Stef=[0.0, 1.0])
    # две слегка разные «пачки», но обе явно ближе к Andrey, чем к Stef
    emb = FakeEmbedder({"0": [0.99, 0.02], "1": [0.99, 0.02], "2": [0.85, 0.30], "3": [0.85, 0.30]})
    pipe = WhisperPipeline(AudioSettings(enabled=True), speaker_registry=reg, embedder=emb)
    segs = [_TimedSegment(i * 2.0, i * 2.0 + 2.0, f"р{i}", "Speaker_1") for i in range(4)]
    pipe._identify_segments("x.wav", segs)
    assert set(s.speaker for s in segs) == {"Andrey"}


def test_pipeline_unknown_segment_inherits_previous(tmp_path):
    reg = _reg(tmp_path, Andrey=[1.0, 0.0])
    # второй сегмент эмбеддер не вернул (слишком короткий) → наследует «Andrey»
    emb = FakeEmbedder({"0": [0.99, 0.0]})
    pipe = WhisperPipeline(AudioSettings(enabled=True), speaker_registry=reg, embedder=emb)
    segs = [_TimedSegment(0.0, 2.0, "раз", "Speaker_1"), _TimedSegment(2.0, 2.2, "два", "Speaker_1")]
    pipe._identify_segments("x.wav", segs)
    assert [s.speaker for s in segs] == ["Andrey", "Andrey"]


def test_diarize_splits_speakers_without_registry():
    """Без записанных голосов реплики всё равно делятся: Speaker_1/Speaker_2."""
    from dirizher.audio.diarize import assign_speakers_by_voice
    from dirizher.audio.transcriber import Segment

    emb = FakeEmbedder({"0": [0.99, 0.02], "1": [0.02, 0.99], "2": [0.97, 0.05]})
    segs = [Segment("Speaker_1", f"реплика {i}", start=i * 2.0, end=i * 2.0 + 2.0) for i in range(3)]
    assert assign_speakers_by_voice("x.wav", segs, emb, registry=None) is True
    # два разных голоса → два кластера; первый и третий — один и тот же говорящий
    assert segs[0].speaker == segs[2].speaker
    assert segs[0].speaker != segs[1].speaker
    assert {s.speaker for s in segs} == {"Speaker_1", "Speaker_2"}


def test_diarize_name_threshold_guards_false_match(tmp_path):
    """Слабый матч (0.72 < score < 0.82) НЕ называется именем — остаётся Speaker_N.

    Это и есть защита от бага «все реплики → единственный записанный голос»:
    схлопнутый кластер не должен уверенно присваиваться записанному человеку.
    """
    from dirizher.audio.diarize import assign_speakers_by_voice
    from dirizher.audio.transcriber import Segment

    reg = _reg(tmp_path, Danya=[1.0, 0.0])  # порог реестра 0.8
    # один кластер, средний вектор близок к Дане, но не настолько (cos≈0.78)
    emb = FakeEmbedder({"0": [0.78, 0.626], "1": [0.78, 0.626]})
    segs = [Segment("Speaker_1", f"р{i}", start=i * 2.0, end=i * 2.0 + 2.0) for i in range(2)]
    assign_speakers_by_voice("x.wav", segs, emb, reg, name_threshold=0.82)
    assert {s.speaker for s in segs} == {"Speaker_1"}  # имя не присвоено

    # с мягким порогом тот же кластер уже называется Даней
    segs2 = [Segment("Speaker_1", f"р{i}", start=i * 2.0, end=i * 2.0 + 2.0) for i in range(2)]
    assign_speakers_by_voice("x.wav", segs2, emb, reg, name_threshold=0.7)
    assert {s.speaker for s in segs2} == {"Danya"}


def test_merge_raw_speakers_collapses_oversplit():
    """pyannote пере-дробил одного на 3 метки → схлопываем по близости в одного."""
    from dirizher.audio.diarize import _merge_raw_speakers

    # SPEAKER_00/02/03 — один голос (близкие векторы), SPEAKER_01 — другой
    emb = {
        "SPEAKER_00": [0.99, 0.02],
        "SPEAKER_01": [0.02, 0.99],
        "SPEAKER_02": [0.98, 0.05],
        "SPEAKER_03": [0.97, 0.06],
    }
    order = ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02", "SPEAKER_03"]
    groups = _merge_raw_speakers(order, emb, sim_threshold=0.80)
    # должно получиться 2 группы: {00,02,03} и {01}
    assert len(groups) == 2
    sizes = sorted(len(g) for g in groups)
    assert sizes == [1, 3]


def test_pyannote_diarization_skipped_without_token():
    """Без HF-токена pyannote-диаризация не запускается (вернёт False)."""
    from dirizher.audio.diarize import assign_speakers_pyannote
    from dirizher.audio.transcriber import Segment

    segs = [Segment("Speaker_1", "раз", start=0.0, end=2.0)]
    assert assign_speakers_pyannote("x.wav", segs, hf_token="") is False


def test_diarize_noop_without_timestamps():
    """Нет таймкодов (плоский текст) → диаризация невозможна, метки не меняем."""
    from dirizher.audio.diarize import assign_speakers_by_voice
    from dirizher.audio.transcriber import Segment

    emb = FakeEmbedder({"0": [1.0, 0.0]})
    segs = [Segment("Speaker_1", "раз"), Segment("Speaker_1", "два")]
    assert assign_speakers_by_voice("x.wav", segs, emb) is False
    assert [s.speaker for s in segs] == ["Speaker_1", "Speaker_1"]


def test_can_identify_false_without_voices(tmp_path):
    empty = SpeakerRegistry(path=str(tmp_path / "vp.json"))
    pipe = WhisperPipeline(AudioSettings(enabled=True), speaker_registry=empty, embedder=FakeEmbedder({}))
    assert pipe._can_identify() is False
    pipe2 = WhisperPipeline(AudioSettings(enabled=True), speaker_registry=_reg(tmp_path, A=[1.0]), embedder=None)
    assert pipe2._can_identify() is False


@pytest.mark.asyncio
async def test_meeting_label_shows_username():
    """После опознания seg.speaker=имя → в саммари показываем @username."""
    team = TeamRegistry()
    team.register(TeamMember(user_id=1, username="stefan_richard", full_name="Энди", aliases=["Стеф"]))

    class _Svc:
        def __init__(self, team):
            self.team = team

    svc = MeetingService.__new__(MeetingService)
    svc.service = _Svc(team)
    tr = TranscriptResult(
        text="",
        segments=[Segment(speaker="Энди", text="сделаю лабу"), Segment(speaker="Speaker_2", text="ок")],
    )
    labeled = svc._label_speakers(tr)
    assert "@stefan_richard: сделаю лабу" in labeled
    assert "Speaker_2: ок" in labeled  # неопознанный остаётся как есть


def test_build_embedder_selection():
    assert build_embedder(AudioSettings(enabled=False)) is None
    assert build_embedder(AudioSettings(enabled=True, backend="groq")).name == "signal"
    assert build_embedder(AudioSettings(enabled=True, backend="local")).name == "signal"  # без токена
    prem = build_embedder(AudioSettings(enabled=True, backend="local", hf_token="hf_x"))
    assert prem.name == "pyannote"  # модель грузится лениво, тут не трогаем


def test_signal_embedder_separates_real_voices(tmp_path):
    """Реальный signal-эмбеддер: один голос узнаётся, чужой — отвергается."""
    emb = SignalEmbedder()

    def voice(f0, seed, dur=4.0, sr=16000):
        rng = random.Random(seed)
        phases = {m: rng.random() for m, _a in [(1, 1.0), (2, 0.5), (3, 0.3), (5, 0.15)]}
        sig = []
        for i in range(int(sr * dur)):
            t = i / sr
            val = sum(
                a * math.sin(2 * math.pi * f0 * m * t + phases[m])
                for m, a in [(1, 1.0), (2, 0.5), (3, 0.3), (5, 0.15)]
            )
            val += 0.02 * rng.gauss(0.0, 1.0)
            sig.append(val)
        peak = max(abs(x) for x in sig) or 1.0
        return [x / peak for x in sig]

    def wav(sig, idx):
        p = str(tmp_path / f"voice-{idx}.wav")
        frames = bytearray()
        for value in sig:
            sample = int(max(-1.0, min(1.0, value)) * 32767)
            frames.extend(sample.to_bytes(2, "little", signed=True))
        with wave.open(p, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(bytes(frames))
        return p

    a1 = emb.embed_file(wav(voice(120, 1), 1))
    a2 = emb.embed_file(wav(voice(120, 2), 2))
    b1 = emb.embed_file(wav(voice(210, 3), 3))

    def cos(x, y):
        dot = sum(a * b for a, b in zip(x, y))
        nx = math.sqrt(sum(a * a for a in x))
        ny = math.sqrt(sum(b * b for b in y))
        return dot / (nx * ny)

    assert cos(a1, a2) > cos(a1, b1) + 0.2  # тот же голос заметно ближе, чем чужой

    reg = SpeakerRegistry(path=str(tmp_path / "vp.json"), threshold=0.75)
    reg.enroll("A", a1)
    reg.enroll("B", b1)
    assert reg.identify(a2) == "A"
