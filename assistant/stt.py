"""Распознавание речи (Whisper через faster-whisper, локально)."""
import io
import os
import site
import threading
import wave
from pathlib import Path

import numpy as np

from . import settings


def _add_cuda_dlls():
    # pip-пакеты nvidia-cublas/cudnn кладут DLL в site-packages/nvidia/*/bin
    for sp in site.getsitepackages():
        base = Path(sp) / "nvidia"
        if base.exists():
            for bin_dir in base.glob("*/bin"):
                try:
                    os.add_dll_directory(str(bin_dir))
                    os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ["PATH"]
                except Exception:
                    pass


_add_cuda_dlls()

_model = None
_model_key = None
_lock = threading.Lock()
device_info = "не загружено"


def _load():
    global _model, _model_key, device_info
    from faster_whisper import WhisperModel

    s = settings.get()
    name, dev = s.get("stt_model", "large-v3-turbo"), s.get("stt_device", "auto")
    if _model is not None and _model_key == (name, dev):
        return _model
    # int8_float16: почти та же точность, но ~вдвое меньше видеопамяти
    attempts = [("cuda", "int8_float16"), ("cpu", "int8")] if dev == "auto" else \
        [(dev, "int8_float16" if dev == "cuda" else "int8")]
    last = None
    for d, ct in attempts:
        try:
            print(f"[STT] Загружаю Whisper {name} на {d}...")
            m = WhisperModel(name, device=d, compute_type=ct)
            # прогон, чтобы сразу поймать ошибки CUDA
            m.transcribe(np.zeros(16000, dtype=np.float32), language="ru")
            _model, _model_key = m, (name, dev)
            device_info = f"{name} / {d}"
            print("[STT] Готово:", device_info)
            return m
        except Exception as e:
            print(f"[STT] Не удалось на {d}: {e}")
            last = e
    raise RuntimeError(f"Whisper не загрузился: {last}")


def _decode_wav(data: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(data), "rb") as w:
        sr, ch = w.getframerate(), w.getnchannels()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    audio = pcm.astype(np.float32) / 32768.0
    if ch > 1:
        audio = audio.reshape(-1, ch).mean(axis=1)
    if sr != 16000:
        n = int(len(audio) * 16000 / sr)
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio)
    return audio.astype(np.float32)


def transcribe(wav_bytes: bytes) -> str:
    audio = _decode_wav(wav_bytes)
    if len(audio) < 16000 * 0.25:
        return ""
    with _lock:
        model = _load()
        s = settings.get()
        # подсказка словаря: имя ассистента и слова, которые Whisper часто путает (Стим/стрим и т.п.)
        hint = (f"{s['assistant_name']}, привет. Открой Steam, Discord, YouTube, GitHub, Telegram, браузер. "
                f"{s.get('stt_hint', '')}")
        segments, _ = model.transcribe(
            audio, language="ru", beam_size=5, vad_filter=True, initial_prompt=hint,
            condition_on_previous_text=False, no_speech_threshold=0.6,
        )
        text = " ".join(seg.text.strip() for seg in segments if seg.no_speech_prob < 0.7)
    return text.strip()


def preload():
    with _lock:
        _load()
