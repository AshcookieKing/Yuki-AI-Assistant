"""Связь с голосовым процессом singer/worker.py: перенос голоса по образцу и пение каверов."""
import asyncio
import os
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

from . import settings
from .settings import DATA_DIR, ROOT

WORKER_URL = "http://127.0.0.1:8766"
PYTHON = ROOT / ".venv-sing" / "Scripts" / "python.exe"
SCRIPT = ROOT / "singer" / "worker.py"
REF = DATA_DIR / "voice_ref.wav"

_proc: subprocess.Popen | None = None
_start_lock = asyncio.Lock()  # фразы озвучиваются параллельно — без замка каждая запускала свой процесс


def available() -> bool:
    return PYTHON.exists() and SCRIPT.exists()


async def _alive() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2) as c:
            return (await c.get(WORKER_URL + "/progress")).status_code == 200
    except Exception:
        return False


async def ensure_worker():
    async with _start_lock:
        await _ensure_worker()


async def _ensure_worker():
    global _proc
    if await _alive():
        return
    if _proc and _proc.poll() is None:
        # уже запускается — просто ждём
        for _ in range(240):
            if await _alive():
                return
            await asyncio.sleep(0.5)
    if not available():
        raise RuntimeError("Голосовой модуль не установлен (запусти setup_voice.bat)")
    log = open(DATA_DIR / "voice_worker.log", "a", encoding="utf-8")
    _proc = subprocess.Popen([str(PYTHON), str(SCRIPT)], stdout=log, stderr=log,
                             creationflags=subprocess.CREATE_NO_WINDOW,
                             env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    for _ in range(120):
        if await _alive():
            return
        await asyncio.sleep(0.5)
    raise RuntimeError("Голосовой модуль не запустился, см. data/voice_worker.log")


def stop_worker():
    if _proc and _proc.poll() is None:
        subprocess.run(["taskkill", "/pid", str(_proc.pid), "/T", "/F"], capture_output=True)


async def free_llm_vram():
    """Выгружаем модель Ollama, чтобы видеокарте хватило памяти на пение. Потом она загрузится сама."""
    s = settings.get()
    if s.get("llm_provider", "ollama") != "ollama":
        return
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            await c.post(f"{s['ollama_url']}/api/generate", json={"model": s["llm_model"], "keep_alive": 0})
    except Exception:
        pass


async def ensure_reference(make_tts):
    """Если образца голоса нет — делаем его из текущего синтеза (её нынешний голос)."""
    if REF.exists():
        return
    text = ("Привет! Я Юки, и я живу в твоём компьютере. Сегодня отличный день, правда? "
            "Давай я тебе что-нибудь спою, а ты похлопаешь. Ну же, не стесняйся!")
    from .tts import ensure_wav

    audio = await make_tts(text)
    REF.write_bytes(ensure_wav(audio))


async def convert(wav: bytes) -> bytes:
    """Переносим голос фразы на образец. Если что-то пошло не так — возвращаем исходный звук."""
    await ensure_worker()
    with tempfile.TemporaryDirectory() as d:
        src, out = Path(d) / "src.wav", Path(d) / "out.wav"
        from .tts import ensure_wav

        src.write_bytes(ensure_wav(wav))
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(WORKER_URL + "/convert", json={
                "source": str(src), "target": str(REF), "output": str(out),
                "steps": int(settings.get().get("clone_steps", 10))})
        if r.status_code != 200 or not out.exists():
            return wav
        return out.read_bytes()


async def sing(query: str, on_progress) -> dict:
    await ensure_worker()
    await free_llm_vram()
    done = asyncio.Event()

    async def poll():
        last = ""
        while not done.is_set():
            try:
                async with httpx.AsyncClient(timeout=3) as c:
                    p = (await c.get(WORKER_URL + "/progress")).json()
                if p.get("detail") and p["detail"] != last:
                    last = p["detail"]
                    await on_progress(last)
            except Exception:
                pass
            await asyncio.sleep(1.5)

    poller = asyncio.create_task(poll())
    t = time.time()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(900, connect=5)) as c:
            r = await c.post(WORKER_URL + "/sing", json={
                "query": query, "target": str(REF), "max_seconds": int(settings.get().get("sing_seconds", 90)),
                "reverb": float(settings.get().get("sing_reverb", 0.5)), "steps": int(settings.get().get("sing_steps", 30))})
    finally:
        done.set()
        await poller
    j = r.json()
    if r.status_code != 200:
        raise RuntimeError(j.get("error", "ошибка"))
    j["seconds"] = round(time.time() - t)
    return j


# ---------- нейро-голос Chatterbox (отдельный процесс в .venv-tts, порт 8767) ----------
TTS_URL = "http://127.0.0.1:8767"
TTS_PYTHON = ROOT / ".venv-tts" / "Scripts" / "python.exe"
TTS_SCRIPT = ROOT / "singer" / "tts_worker.py"
TTS_REF = DATA_DIR / "voice_ref_tts.wav"   # образец тембра для Chatterbox (если нет — берём общий)
_tts_proc: subprocess.Popen | None = None
_tts_lock = asyncio.Lock()


def tts_available() -> bool:
    return TTS_PYTHON.exists() and TTS_SCRIPT.exists()


async def _tts_alive() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2) as c:
            return (await c.get(TTS_URL + "/")).status_code == 200
    except Exception:
        return False


async def ensure_tts():
    global _tts_proc
    async with _tts_lock:
        if await _tts_alive():
            return
        if not tts_available():
            raise RuntimeError("Нейро-голос не установлен (setup_voice.bat)")
        if not (_tts_proc and _tts_proc.poll() is None):
            log = open(DATA_DIR / "tts_worker.log", "a", encoding="utf-8")
            _tts_proc = subprocess.Popen([str(TTS_PYTHON), str(TTS_SCRIPT)], stdout=log, stderr=log,
                                         creationflags=subprocess.CREATE_NO_WINDOW,
                                         env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        for _ in range(240):
            if await _tts_alive():
                return
            await asyncio.sleep(0.5)
        raise RuntimeError("Нейро-голос не запустился, см. data/tts_worker.log")


async def neural_tts(text: str) -> bytes:
    await ensure_tts()
    s = settings.get()
    ref = TTS_REF if TTS_REF.exists() else REF
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(TTS_URL + "/tts", json={
            "text": text, "ref": str(ref), "exaggeration": float(s.get("tts_exaggeration", 0.7)),
            "cfg": float(s.get("tts_cfg", 0.35)), "temperature": float(s.get("tts_temperature", 0.8))})
    if r.status_code != 200:
        raise RuntimeError(r.text[:200])
    return r.content


def stop_tts():
    if _tts_proc and _tts_proc.poll() is None:
        subprocess.run(["taskkill", "/pid", str(_tts_proc.pid), "/T", "/F"], capture_output=True)
