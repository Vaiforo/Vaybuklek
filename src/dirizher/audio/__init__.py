"""Аудио-слой: распознавание речи со встреч и голосовых (Этап 3)."""

import warnings

# pyannote при импорте пытается загрузить torchcodec для встроенного декодинга и,
# не найдя FFmpeg-DLL на Windows, печатает огромный traceback-варнинг. Нам он не
# нужен: аудио мы декодим сами (PyAV/soundfile) и отдаём pyannote waveform в памяти
# (см. pyannote_compat.to_pyannote_audio). Глушим этот и сопутствующий TF32-шум,
# чтобы не засорять логи. (?s) — чтобы точка матчила многострочное сообщение.
warnings.filterwarnings("ignore", message=r"(?s).*torchcodec.*")
warnings.filterwarnings("ignore", message=r"(?s).*TensorFloat-32.*")
warnings.filterwarnings("ignore", message=r"(?s).*degrees of freedom is <= 0.*")

from .transcriber import Transcriber, TranscriptResult, build_transcriber  # noqa: E402

__all__ = ["Transcriber", "TranscriptResult", "build_transcriber"]
