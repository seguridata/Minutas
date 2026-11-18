"""Transcripción local con faster-whisper; el audio nunca sale del equipo."""

import os
from collections.abc import Callable

import numpy as np
from faster_whisper import WhisperModel, decode_audio

from .config import WHISPER_DEVICE, WHISPER_MODEL

_model: WhisperModel | None = None


def _get_model(device: str = WHISPER_DEVICE) -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(WHISPER_MODEL, device=device, compute_type="int8")
    return _model


def _is_cuda_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in ("cublas", "cudnn", "cuda"))


def _decode(source) -> np.ndarray | None:
    """Una grabación propia (cifrada, se descifra al vuelo) o un archivo de audio del usuario."""
    if hasattr(source, "open"):
        with source.open() as audio:
            return decode_audio(audio, sampling_rate=16000) if audio.data_size else None
    return decode_audio(source, sampling_rate=16000) if os.path.getsize(source) > 44 else None


def _mix(paths: list) -> np.ndarray:
    tracks = [t for t in (_decode(p) for p in paths) if t is not None]
    if not tracks:
        return np.zeros(0, dtype=np.float32)
    length = max(len(t) for t in tracks)
    mixed = np.zeros(length, dtype=np.float32)
    for t in tracks:
        mixed[: len(t)] += t
    return np.clip(mixed, -1.0, 1.0)


def _ts(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def transcribe(paths: list, on_progress: Callable[[float], None] = lambda p: None) -> str:
    audio = _mix(paths)
    if audio.size == 0:
        return ""
    duration = len(audio) / 16000

    def run(model: WhisperModel) -> str:
        segments, _ = model.transcribe(audio, language="es", vad_filter=True, beam_size=5)
        lines = []
        for seg in segments:  # el generador es perezoso: los errores de CUDA salen aquí
            lines.append(f"[{_ts(seg.start)}] {seg.text.strip()}")
            on_progress(min(seg.end / duration, 1.0))
        return "\n".join(lines)

    global _model
    try:
        text = run(_get_model())
    except RuntimeError as exc:
        # Equipos con GPU NVIDIA pero sin las bibliotecas de CUDA: se continúa en CPU
        if WHISPER_DEVICE != "auto" or not _is_cuda_error(exc):
            raise
        _model = None
        text = run(_get_model("cpu"))
    on_progress(1.0)
    return text
