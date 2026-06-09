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

from .decode import decode_mono16k


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

    samples, sr = decode_mono16k(path)
    wav = torch.from_numpy(np.ascontiguousarray(samples, dtype=np.float32)).reshape(1, -1)
    return {"waveform": wav, "sample_rate": sr}
