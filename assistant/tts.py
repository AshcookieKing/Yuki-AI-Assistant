"""Синтез речи: Silero (офлайн) или Edge TTS (онлайн), плюс «аниме-фильтр» на Praat:
поднимает тон и форманты (моложе тембр) и усиливает интонацию — без эффекта бурундука."""
import asyncio
import io
import os
import tempfile
import threading
import urllib.request
import wave
from xml.sax.saxutils import escape

import numpy as np

from . import settings
from .settings import DATA_DIR

SILERO_URL = "https://models.silero.ai/models/tts/ru/{name}.pt"
SILERO_SPEAKERS = ["baya", "kseniya", "xenia", "aidar", "eugene"]
EDGE_VOICES = ["ru-RU-SvetlanaNeural", "en-US-EmmaMultilingualNeural", "en-US-AvaMultilingualNeural",
               "fr-FR-VivienneMultilingualNeural", "de-DE-SeraphinaMultilingualNeural", "ru-RU-DmitryNeural"]
SR = 48000

_PITCH = ["x-low", "low", "medium", "high", "x-high"]

# Готовые варианты аниме-фильтра: (средний тон Гц, сдвиг формант, выразительность)
ANIME_PRESETS = {
    "Выкл": (0, 1.0, 1.0),
    "Мягкая": (300, 1.10, 1.3),
    "Кавай": (340, 1.15, 1.5),
    "Лоли": (380, 1.20, 1.6),
    "Нейро": (365, 1.17, 1.9),
}

_model = None
_model_name = None
_lock = threading.Lock()


def _silero_model(name: str):
    global _model, _model_name
    if _model is not None and _model_name == name:
        return _model
    import torch

    torch.set_num_threads(4)
    path = DATA_DIR / f"silero_{name}.pt"
    if not path.exists():
        print(f"[TTS] Скачиваю модель Silero {name}...")
        urllib.request.urlretrieve(SILERO_URL.format(name=name), path)
    imp = torch.package.PackageImporter(str(path))
    model = imp.load_pickle("tts_models", "model")
    model.to(torch.device("cpu"))
    _model, _model_name = model, name
    return model


def _rate_word(rate: float) -> str:
    if rate < 0.7:
        return "x-slow"
    if rate < 0.9:
        return "slow"
    if rate <= 1.1:
        return "medium"
    return "fast" if rate <= 1.35 else "x-fast"


def _to_wav(audio: np.ndarray, sr: int) -> bytes:
    peak = float(np.abs(audio).max()) if len(audio) else 0
    if peak > 0.98:
        audio = audio / peak * 0.95
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def anime_filter(snd, s: dict):
    """snd — parselmouth.Sound. Возвращает numpy-массив."""
    from parselmouth.praat import call

    median, formant, rng = float(s.get("anime_pitch", 0)), float(s.get("anime_formant", 1.0)), \
        float(s.get("anime_expressive", 1.0))
    if median <= 0 and abs(formant - 1) < 0.01 and abs(rng - 1) < 0.01:
        return snd.values[0]
    out = call(snd, "Change gender", 75, 600, formant, median, rng, 1.0)
    return out.values[0]


def _silero(text: str, s: dict) -> bytes:
    import parselmouth

    p = int(max(0, min(4, s.get("voice_pitch", 2))))
    ssml = (f'<speak><prosody pitch="{_PITCH[p]}" rate="{_rate_word(float(s.get("voice_rate", 1.0)))}">'
            f'{escape(text)}</prosody></speak>')
    with _lock:
        model = _silero_model(s.get("silero_model", "v4_ru"))
        speaker = s.get("silero_speaker", "baya")
        try:
            audio = model.apply_tts(ssml_text=ssml, speaker=speaker, sample_rate=SR,
                                    put_accent=True, put_yo=True)
        except Exception:
            audio = model.apply_tts(text=text, speaker=speaker, sample_rate=SR,
                                    put_accent=True, put_yo=True)
    audio = audio.numpy().astype(np.float64)
    audio = anime_filter(parselmouth.Sound(audio, SR), s)
    return _to_wav(audio.astype(np.float32), SR)


async def _edge(text: str, s: dict) -> bytes:
    import edge_tts
    import parselmouth

    rate = f"{int((float(s.get('voice_rate', 1.0)) - 1) * 100):+d}%"
    # тон меняет сама нейросеть Microsoft — это чище, чем сдвиг фильтром
    pitch = f"{int(s.get('edge_pitch', 35)):+d}Hz"
    com = edge_tts.Communicate(text, s.get("edge_voice", EDGE_VOICES[0]), rate=rate, pitch=pitch)
    mp3 = bytearray()
    async for chunk in com.stream():
        if chunk["type"] == "audio":
            mp3.extend(chunk["data"])

    # фильтр выключен — отдаём звук нейросети как есть, без перекодирования и обработки
    if (float(s.get("anime_pitch", 0)) <= 0 and abs(float(s.get("anime_formant", 1)) - 1) < 0.01
            and abs(float(s.get("anime_expressive", 1)) - 1) < 0.01):
        return bytes(mp3)

    def post():
        fd, path = tempfile.mkstemp(suffix=".mp3")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(mp3)
            snd = parselmouth.Sound(path)
            return _to_wav(anime_filter(snd, s).astype(np.float32), int(snd.sampling_frequency))
        finally:
            os.remove(path)

    return await asyncio.to_thread(post)


def ensure_wav(audio: bytes) -> bytes:
    """MP3 от Edge -> WAV (нужно для образца голоса и переноса голоса)."""
    if audio[:4] == b"RIFF":
        return audio
    import parselmouth

    fd, path = tempfile.mkstemp(suffix=".mp3")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(audio)
        snd = parselmouth.Sound(path)
        return _to_wav(snd.values[0].astype(np.float32), int(snd.sampling_frequency))
    finally:
        os.remove(path)


async def synthesize_base(text: str) -> bytes | None:
    s = settings.get()
    if not text or not any(ch.isalpha() for ch in text):
        return None
    if s.get("tts_engine") == "edge":
        try:
            return await _edge(text, s)
        except Exception as e:
            print("[TTS] Edge недоступен, переключаюсь на Silero:", e)
    return await asyncio.to_thread(_silero, text, s)


async def synthesize(text: str) -> bytes | None:
    s = settings.get()
    if s.get("tts_engine") == "chatterbox" and text and any(ch.isalpha() for ch in text):
        # нейро-голос сам клонирует тембр образца — перенос голоса не нужен
        from . import voice

        try:
            return await voice.neural_tts(text)
        except Exception as e:
            print("[TTS] нейро-голос не ответил, говорю запасным:", e)
            return await synthesize_base(text)
    if s.get("voice_clone") and s.get("clone_source", "silero") == "silero" and text and any(ch.isalpha() for ch in text):
        # тембр всё равно заменит образец — берём быстрый локальный Silero без фильтров как «исходник»
        clean = {**s, "anime_pitch": 0, "anime_formant": 1.0, "anime_expressive": 1.0, "voice_pitch": 2}
        audio = await asyncio.to_thread(_silero, text, clean)
    else:
        audio = await synthesize_base(text)
    if audio and s.get("voice_clone"):
        from . import voice

        if voice.REF.exists():
            try:
                audio = await voice.convert(audio)
            except Exception as e:
                print("[TTS] перенос голоса не удался:", e)
    return audio


def preload():
    _silero_model(settings.get().get("silero_model", "v4_ru"))
