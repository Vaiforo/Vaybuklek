"""Совместимость с pyannote.audio: версии API и обход torchcodec.

Две практические проблемы, особенно на Windows:

1. Аргумент токена у `*.from_pretrained` переименовали: новые версии ждут
   `token=`, старые — `use_auth_token=`. `load_pretrained` пробует оба.
2. Встроенное декодирование аудио в pyannote тянет `torchcodec`, который на
   Windows часто не находит ffmpeg-DLL и падает. Поэтому НЕ передаём pyannote
   путь к файлу, а грузим аудио сами (soundfile/PyAV из decode.py) и отдаём как
   waveform в памяти — это полностью обходит torchcodec.
"""

from __future__ import annotations

import os

from ..logging_setup import get_logger

log = get_logger("dirizher.audio.pyannote")


def _cuda_available() -> bool:
    """Есть ли рабочий CUDA-GPU. torch — мягкая зависимость (может быть CPU-сборкой)."""
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001
        return False


def resolve_device(pref: str = "auto") -> str:
    """Свести предпочтение пользователя к фактическому устройству: 'cuda' | 'cpu'.

    Единая точка выбора для ВСЕГО аудио-стека (Whisper + pyannote), чтобы они не
    разъезжались. pref: 'auto' (CUDA если есть, иначе CPU) | 'cuda' (с откатом и
    предупреждением, если CUDA нет) | 'cpu' (принудительно процессор).
    """
    pref = (pref or "auto").strip().lower()
    if pref == "cpu":
        return "cpu"
    if pref == "cuda":
        if _cuda_available():
            return "cuda"
        log.warning("DEVICE=cuda, но CUDA/torch недоступны — откат на CPU")
        return "cpu"
    # auto и любые неизвестные значения
    return "cuda" if _cuda_available() else "cpu"


def cuda_device(pref: str = "auto"):
    """torch.device('cuda') если устройство сводится к CUDA, иначе None.

    None означает «оставить на CPU» (pyannote Inference по умолчанию на CPU).
    """
    if resolve_device(pref) != "cuda":
        return None
    try:
        import torch

        return torch.device("cuda")
    except Exception:  # noqa: BLE001
        return None


def move_to_cuda(obj, label: str, pref: str = "auto") -> bool:
    """Перенести pyannote Pipeline/Inference на GPU, если устройство = CUDA.

    pyannote по умолчанию считает на CPU — для длинных встреч это в разы медленнее.
    При pref='cpu' или отсутствии CUDA остаёмся на CPU. Возвращает True, если
    реально перенесли на GPU.
    """
    dev = cuda_device(pref)
    if dev is None or obj is None:
        return False
    try:
        obj.to(dev)
        log.info("%s → GPU (cuda)", label)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("%s остаётся на CPU (%s)", label, e)
        return False


def load_pretrained(from_pretrained, name: str, token: str):
    """Вызвать `Pipeline/Model.from_pretrained` с токеном, переживая разницу API.

    Токен дублируем в переменные окружения HF: pyannote при загрузке весов
    (pytorch_model.bin) дёргает hf_hub_download БЕЗ явного токена, из-за чего на
    gated-моделях прилетает 401 «Please log in». huggingface_hub берёт токен из
    HF_TOKEN/HUGGING_FACE_HUB_TOKEN — так аутентификация доходит до скачивания.
    """
    if token:
        os.environ["HF_TOKEN"] = token
        os.environ["HUGGING_FACE_HUB_TOKEN"] = token
    try:
        return from_pretrained(name, token=token)
    except TypeError:
        return from_pretrained(name, use_auth_token=token)


def diarization_turns(result) -> list[tuple[float, float, str]]:
    """Список (start, end, speaker) из результата пайплайна, независимо от версии.

    pyannote.audio 4.x возвращает DiarizeOutput (Annotation лежит в
    `.speaker_diarization`); 3.x возвращает Annotation напрямую. Раньше код звал
    `.itertracks` на DiarizeOutput → падал, и диаризация молча отключалась.
    """
    annotation = getattr(result, "speaker_diarization", result)
    return [
        (turn.start, turn.end, speaker)
        for turn, _track, speaker in annotation.itertracks(yield_label=True)
    ]


def to_pyannote_audio(path: str) -> dict:
    """Аудио для pyannote как {'waveform': (1, T) tensor, 'sample_rate': 16000}.

    Декодируем через decode_mono16k (soundfile для WAV, PyAV для остального) —
    без torchcodec/ffmpeg-бинарника. pyannote-модели работают на 16 кГц.
    """
    import numpy as np
    import torch

    from .decode import decode_mono16k

    samples, sr = decode_mono16k(path)
    wav = torch.from_numpy(np.ascontiguousarray(samples, dtype=np.float32)).reshape(1, -1)
    return {"waveform": wav, "sample_rate": sr}
