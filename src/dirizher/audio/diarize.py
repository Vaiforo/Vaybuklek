"""Разделение реплик по голосу (диаризация) поверх готовых сегментов.

Общий модуль и для локального пайплайна (faster-whisper), и для облачного пути
(Groq Whisper): на вход — сегменты с таймкодами + путь к аудио, на выходе у
каждого сегмента проставлен `speaker`.

Идея (механика из отчёта, раздел 3.3): MFCC-эмбеддинг на коротком сегменте
шумный, решать по каждому отдельно нельзя. Поэтому:
1) кластеризуем эмбеддинги сегментов по похожести голоса (агломеративно по
   косинусу) — это и есть «кто-когда говорил»;
2) реестр голосов используем лишь чтобы НАЗВАТЬ кластеры (средний эмбеддинг
   кластера → ближайший зарегистрированный → имя; иначе Speaker_N).

Реестр может быть пустым — тогда просто получаем Speaker_1/Speaker_2/… по числу
разных голосов. Это и чинит «бот плохо разделяет реплики»: разделение теперь
работает даже без предварительной регистрации голосов.
"""

from __future__ import annotations

import math

from ..logging_setup import get_logger

log = get_logger("dirizher.audio.diarize")

# Порог среза кластеризации, если реестр не задан (или пуст). Согласован с
# дефолтным voiceprint_threshold: голоса ближе порога считаем одним говорящим.
_DEFAULT_THRESHOLD = 0.72

# Косинус-близость, выше которой двух «спикеров» pyannote считаем одним человеком
# и схлопываем (лечит пере-сегментацию на loopback). Консервативно высокий, чтобы
# не склеить разных людей: у нейроэмбеддингов один голос даёт ~0.85+, разные — ниже.
_MERGE_THRESHOLD = 0.80


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 1.0 - (dot / (na * nb)) if na and nb else 1.0


def _mean_vec(vectors: list[list[float]]) -> list[float]:
    n = len(vectors)
    length = len(vectors[0])
    return [sum(v[k] for v in vectors) / n for k in range(length)]


def _cluster(vecs: list[list[float]], cut: float) -> list[int]:
    """Агломеративная кластеризация эмбеддингов по косинус-дистанции.

    Сливаем ближайшие кластеры, пока расстояние между ними меньше `cut`. Если
    все сегменты похожи — выйдет один кластер (монолог не дробится).
    """
    if len(vecs) <= 1:
        return [0] * len(vecs)

    clusters: list[list[int]] = [[i] for i in range(len(vecs))]

    def average_distance(left: list[int], right: list[int]) -> float:
        pairs = [_cosine_distance(vecs[i], vecs[j]) for i in left for j in right]
        return sum(pairs) / len(pairs)

    while len(clusters) > 1:
        best: tuple[float, int, int] | None = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                dist = average_distance(clusters[i], clusters[j])
                if best is None or dist < best[0]:
                    best = (dist, i, j)
        if best is None or best[0] > cut:
            break
        _dist, i, j = best
        clusters[i].extend(clusters[j])
        del clusters[j]

    labels = [0] * len(vecs)
    for cid, members in enumerate(clusters):
        for i in members:
            labels[i] = cid
    return labels


def _best_name(registry, vec: list[float], min_score: float) -> str | None:
    """Имя ближайшего записанного голоса, только если близость ≥ min_score.

    Отдельный (более строгий) порог именно для авто-именования на встречах: на
    шумном loopback-миксе MFCC легко даёт ложный матч и приписывает все реплики
    единственному записанному голосу. Лучше оставить анонимный Speaker_N, чем
    уверенно назвать не того.
    """
    if registry is None or not len(registry):
        return None
    ranked = registry.rank(vec)  # [(name, score)] по убыванию
    if ranked and ranked[0][1] >= min_score:
        return ranked[0][0]
    return None


def assign_speakers_by_voice(
    wav_path: str, segments: list, embedder, registry=None, name_threshold: float | None = None
) -> bool:
    """Проставить `speaker` каждому сегменту по кластеризации голосов.

    Возвращает True, если разметка проставлена. Сегменты должны нести таймкоды
    (`start`/`end`); те, у кого их нет, пропускаются. Незаписанные/слишком
    короткие сегменты наследуют имя соседа сверху. `name_threshold` — строгий
    порог авто-именования по реестру (по умолчанию = порогу реестра).
    """
    if embedder is None:
        return False
    timed = [(i, s) for i, s in enumerate(segments) if s.start is not None and s.end is not None]
    if not timed:
        return False

    turns = [(s.start, s.end, str(i)) for i, s in timed]
    try:
        embs = embedder.embed_turns(wav_path, turns)
    except Exception as e:  # noqa: BLE001
        log.warning("Диаризация по голосу пропущена (%s)", e)
        return False

    idxs = [i for i, _s in timed if str(i) in embs]
    if not idxs:
        return False
    vecs = [embs[str(i)] for i in idxs]

    threshold = registry.threshold if registry is not None else _DEFAULT_THRESHOLD
    cut = max(0.05, 1.0 - threshold)  # косинус-дистанция среза
    labels = _cluster(vecs, cut)

    min_score = name_threshold if name_threshold is not None else threshold
    cluster_name: dict[int, str] = {}
    anon = 0
    for cid in sorted(set(labels)):
        members = [vecs[j] for j, lab in enumerate(labels) if lab == cid]
        name = _best_name(registry, _mean_vec(members), min_score)
        if name is None:
            anon += 1
            name = f"Speaker_{anon}"
        cluster_name[cid] = name

    seg_name = {i: cluster_name[labels[j]] for j, i in enumerate(idxs)}
    last = next(iter(cluster_name.values()))
    for i, s in enumerate(segments):
        s.speaker = seg_name.get(i, last)
        last = s.speaker
    return True


# ── Настоящая диаризация pyannote (работает поверх любого STT, включая Groq) ──
_PYANNOTE_PIPELINE = None
_PYANNOTE_KEY: str | None = None


def _get_pyannote(hf_token: str, model: str = "pyannote/speaker-diarization-3.1"):
    """Лениво загрузить и закэшировать pyannote-пайплайн (загрузка медленная)."""
    global _PYANNOTE_PIPELINE, _PYANNOTE_KEY
    if _PYANNOTE_PIPELINE is not None and _PYANNOTE_KEY == hf_token:
        return _PYANNOTE_PIPELINE
    from pyannote.audio import Pipeline

    from .pyannote_compat import load_pretrained

    log.info("Загружаю pyannote 3.1 (диаризация встречи)…")
    _PYANNOTE_PIPELINE = load_pretrained(Pipeline.from_pretrained, model, hf_token)
    _PYANNOTE_KEY = hf_token
    return _PYANNOTE_PIPELINE


def _overlap_speaker(turns: list, seg) -> str:
    """Метка спикера pyannote с максимальным перекрытием по времени с сегментом."""
    best, best_ov = "Speaker_1", 0.0
    for start, end, speaker in turns:
        ov = max(0.0, min(seg.end, end) - max(seg.start, start))
        if ov > best_ov:
            best, best_ov = speaker, ov
    return best


def assign_speakers_pyannote(
    wav_path: str,
    segments: list,
    hf_token: str,
    embedder=None,
    registry=None,
    name_threshold: float | None = None,
    merge_threshold: float = _MERGE_THRESHOLD,
) -> bool:
    """Диаризация pyannote: кто-когда нейросетью, затем имена по реестру голосов.

    Качественно разделяет говорящих даже на смешанном loopback-потоке (в отличие
    от MFCC-кластеризации). Требует HF-токен (бесплатный) и пакет pyannote.audio.
    Возвращает False, если токена/пакета нет или диаризация не удалась — тогда
    вызывающий код откатывается на MFCC.
    """
    if not hf_token:
        return False
    timed = [s for s in segments if s.start is not None and s.end is not None]
    if not timed:
        return False
    try:
        from .pyannote_compat import diarization_turns, to_pyannote_audio

        turns = diarization_turns(_get_pyannote(hf_token)(to_pyannote_audio(wav_path)))
    except Exception as e:  # noqa: BLE001
        log.warning("pyannote-диаризация недоступна (%s) — откат на MFCC", e)
        return False
    if not turns:
        return False

    for s in segments:
        if s.start is not None and s.end is not None:
            s.speaker = _overlap_speaker(turns, s)

    raw_order: list[str] = []
    for _st, _en, spk in turns:
        if spk not in raw_order:
            raw_order.append(spk)
    embeddings: dict[str, list[float]] = {}
    if embedder is not None:
        try:
            embeddings = embedder.embed_turns(wav_path, turns)
        except Exception as e:  # noqa: BLE001
            log.warning("Эмбеддинги спикеров недоступны (%s)", e)
    threshold = registry.threshold if registry is not None else _DEFAULT_THRESHOLD
    min_score = name_threshold if name_threshold is not None else threshold

    raw_to_name = name_raw_speakers(
        raw_order, embeddings, registry, min_score, merge_threshold=merge_threshold
    )
    for s in segments:
        s.speaker = raw_to_name.get(s.speaker, s.speaker)
    return True


def name_raw_speakers(
    raw_order: list[str],
    embeddings: dict[str, list[float]],
    registry,
    min_score: float,
    merge_threshold: float = _MERGE_THRESHOLD,
) -> dict[str, str]:
    """Сырые метки спикеров pyannote → имена. Со схлопыванием пере-сегментации.

    На loopback-миксе pyannote склонен ДРОБИТЬ одного человека на несколько меток
    (видели 8 «спикеров» на ~3 человек). Сначала схлопываем сырых спикеров с очень
    похожими голосовыми эмбеддингами в одну группу, затем называем группу по
    реестру (строгий порог) или Speaker_N. Используется и локальным пайплайном,
    и облачным путём.
    """
    groups = _merge_raw_speakers(raw_order, embeddings, merge_threshold)
    has_registry = registry is not None and len(registry)
    group_name: dict[int, str] = {}
    used: set[str] = set()
    anon = 0
    for gid, members in enumerate(groups):
        vecs = [embeddings[r] for r in members if r in embeddings]
        # Диагностика: какой записанный голос ближайший и с каким косинусом —
        # помогает подобрать DIRIZHER_AUDIO__VOICEPRINT_NAME_THRESHOLD по реальным
        # цифрам (loopback-голос обычно ниже эталона с чистого микрофона).
        if has_registry and vecs:
            ranked = registry.rank(_mean_vec(vecs))
            if ranked:
                top, score = ranked[0]
                log.info(
                    "🎙 группа %d: ближайший голос «%s» cos=%.3f (порог именования %.2f → %s)",
                    gid, top, score, min_score, "ИМЯ" if score >= min_score else "Speaker_N",
                )
        name = _best_name(registry, _mean_vec(vecs), min_score) if vecs else None
        if name is None or name in used:  # один человек — одна метка
            anon += 1
            name = f"Speaker_{anon}"
        used.add(name)
        group_name[gid] = name
    return {r: group_name[gid] for gid, members in enumerate(groups) for r in members}


def _merge_raw_speakers(
    raw_order: list[str], embeddings: dict[str, list[float]], sim_threshold: float
) -> list[list[str]]:
    """Сгруппировать сырых спикеров pyannote, схлопнув похожие голоса в один.

    Спикеров без эмбеддинга (слишком короткие) не мерджим — каждый сам по себе.
    Порядок групп — по первому появлению спикера во времени.
    """
    with_emb = [r for r in raw_order if r in embeddings]
    groups: list[list[str]] = []
    if len(with_emb) >= 2:
        vecs = [embeddings[r] for r in with_emb]
        labels = _cluster(vecs, max(0.05, 1.0 - sim_threshold))
        by_label: dict[int, list[str]] = {}
        for r, lab in zip(with_emb, labels):
            by_label.setdefault(lab, []).append(r)
        groups = list(by_label.values())
    elif with_emb:
        groups = [with_emb]
    for r in raw_order:
        if r not in embeddings:
            groups.append([r])
    groups.sort(key=lambda g: min(raw_order.index(x) for x in g))
    return groups
