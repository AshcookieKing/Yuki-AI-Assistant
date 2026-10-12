"""Веб-сервер ассистента: API, WebSocket и раздача интерфейса.
Окна (фигурка на рабочем столе, панель настроек, браузер) подключаются к одному общему диалогу:
события рассылаются всем, звук играет только окно с ролью speaker."""
import asyncio
import json
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, File, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from assistant import activity, llm, memory, settings, stt, textproc, tools, tts, voice
from assistant.brain import Brain
from assistant.tools import describe

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
MODELS_DIR = STATIC / "models"
PORT = 8765

RECOMMENDED_LLM = [
    {"name": "qwen3.5:9b", "note": "Рекомендую: умная, видит экран, управляет ПК (≈6.6 ГБ)"},
    {"name": "qwen3:8b", "note": "Прошлая версия (≈5 ГБ)"},
    {"name": "qwen3:14b", "note": "Умнее, но тяжелее (≈9 ГБ VRAM)"},
    {"name": "qwen3:4b", "note": "Быстрая и лёгкая (≈2.5 ГБ)"},
    {"name": "qwen3.6:35b-a3b", "note": "Очень умная MoE, часть уйдёт в ОЗУ — медленнее (≈22 ГБ)"},
]

status = {"stt": "загрузка...", "tts": "загрузка..."}


def _preload():
    try:
        tts.preload()
        status["tts"] = "готово"
    except Exception as e:
        status["tts"] = f"ошибка: {e}"
    try:
        stt.preload()
        status["stt"] = stt.device_info
    except Exception as e:
        status["stt"] = f"ошибка: {e}"


async def _warm_voice():
    """Заранее поднимаем голосовые модули, чтобы первая фраза не ждала загрузки."""
    if settings.get().get("tts_engine") == "chatterbox" and voice.tts_available():
        try:
            await voice.ensure_tts()
            async with httpx.AsyncClient(timeout=900) as c:
                await c.post(voice.TTS_URL + "/warmup", json={})
        except Exception as e:
            print("[voice] прогрев нейро-голоса не удался:", e)
        return
    if not (settings.get().get("voice_clone") and voice.available() and voice.REF.exists()):
        return
    try:
        await voice.ensure_worker()
        async with httpx.AsyncClient(timeout=600) as c:
            await c.post(voice.WORKER_URL + "/warmup", json={})
    except Exception as e:
        print("[voice] прогрев не удался:", e)


@asynccontextmanager
async def lifespan(_app):
    settings.load()
    MAIN_LOOP["loop"] = asyncio.get_running_loop()
    asyncio.create_task(activity.run())
    asyncio.create_task(_warm_voice())
    threading.Thread(target=_preload, daemon=True).start()
    yield
    voice.stop_worker()
    voice.stop_tts()


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def no_cache(request: Request, call_next):
    # интерфейс часто обновляется — не даём окнам держать старые скрипты
    resp = await call_next(request)
    if not request.url.path.startswith("/models/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


class Hub:
    """Один диалог на все подключённые окна."""

    def __init__(self):
        self.brain = Brain()
        self.clients: set[WebSocket] = set()
        self.pending: dict[str, asyncio.Future] = {}
        self.task: asyncio.Task | None = None
        self.stop = asyncio.Event()
        self.lock = asyncio.Lock()
        self.singing = False
        self.last_reply_at = 0.0   # когда она в последний раз говорила — после этого 30 с слушает без имени
        self.last_reply_text = ""

    async def emit(self, msg):
        if msg.get("type") in ("done", "speech"):
            self.last_reply_at = time.time()
            if msg.get("type") == "speech":
                self.last_reply_text = msg.get("text", "")
        data = json.dumps(msg, ensure_ascii=False)
        for ws in list(self.clients):
            try:
                await ws.send_text(data)
            except Exception:
                self.clients.discard(ws)

    async def confirm(self, name, args):
        cid = uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        self.pending[cid] = fut
        await self.emit({"type": "confirm_request", "id": cid, "name": name, "detail": describe(name, args)})
        try:
            return await asyncio.wait_for(fut, timeout=120)
        except asyncio.TimeoutError:
            return False
        finally:
            self.pending.pop(cid, None)
            await self.emit({"type": "confirm_done", "id": cid})

    async def stop_current(self):
        t = self.task
        if t and not t.done():
            self.stop.set()
            for f in self.pending.values():
                if not f.done():
                    f.set_result(False)
            try:
                await asyncio.wait_for(t, timeout=5)
            except Exception:
                t.cancel()

    async def rehearsal_reply(self, text):
        """Пока готовится песня, видеокарта занята — отвечаем заготовкой без нейросети."""
        import base64
        import random

        await self.emit({"type": "user", "text": text, "hidden": False})
        line = random.choice(REHEARSAL_LINES)
        audio = await tts.synthesize_base(line)
        await self.emit({"type": "speech", "text": line, "emotion": "shy", "gesture": None,
                         "audio": base64.b64encode(audio).decode() if audio else None})
        await self.emit({"type": "done"})

    def _named(self, text):
        name = settings.get()["assistant_name"].lower()
        stem = name[:2] if len(name) >= 3 else name
        return any(w.startswith(stem) for w in re.findall(r"\w+", text.lower()))

    async def chat(self, text, hidden, voice=False):
        s = settings.get()
        mode = s.get("attention_mode", "smart")
        if voice and not hidden and mode != "always":
            recent = time.time() - self.last_reply_at < float(s.get("followup_seconds", 30))
            # сразу после её ответа короткие фразы — точно ей; длинные всё равно проверяем (вдруг это видео)
            ok = self._named(text) or (recent and len(text) < 60)
            if not ok and mode == "smart":
                from assistant.brain import is_addressed
                ok = await is_addressed(text, self.last_reply_text)
            if not ok:
                await self.emit({"type": "ignored", "text": text})
                return
        if self.singing and not hidden:
            await self.rehearsal_reply(text)
            return
        async with self.lock:
            await self.stop_current()
            self.stop = asyncio.Event()
            await self.emit({"type": "user", "text": text, "hidden": hidden})
            self.task = asyncio.create_task(self.brain.respond(text, self.emit, self.confirm, self.stop, hidden))

    async def autonomy(self, minutes):
        """Она сама решает заговорить: смотрит на экран (если разрешено), вспоминает, о чём говорили."""
        if self.singing or (self.task and not self.task.done()):
            return
        s = settings.get()
        seen = ""
        cloud = s.get("llm_provider", "ollama") != "ollama" or s["llm_model"].endswith("cloud")
        # снимок экрана для самостоятельных реплик в облако не отправляем, если это явно не разрешено
        if s.get("autonomy_screen") and (not cloud or s.get("cloud_screen", False)):
            try:
                img = await asyncio.to_thread(tools.screenshot_b64)
                from assistant.brain import vision
                seen = await vision("Опиши в одном-двух предложениях, чем сейчас занят пользователь на компьютере.", img)
            except Exception:
                seen = ""
        await self.chat(
            f"(Системное сообщение, не цитируй его: пользователь молчит уже {minutes} мин. "
            + (f"Ты подсмотрела на его экран: {seen} " if seen else "")
            + (activity.now_text() + " " if s.get("learn_activity", True) else "")
            + "Заговори сама, как живая собеседница на стриме: прокомментируй, что он делает, вспомни что-то из прошлых "
              "разговоров, задай вопрос, подколи или поделись своей мыслью. Одно-два предложения.)", True)


REHEARSAL_LINES = [
    "Тсс! Я репетирую, подожди минутку.",
    "Не отвлекай, я распеваюсь! Сейчас будет шедевр.",
    "Минуточку, голосовые связки разогреваются.",
    "Почти готово, не торопи артистку!",
]

hub = Hub()


# ---------- напоминания ----------
def _remind(text, seconds):
    loop = MAIN_LOOP["loop"]  # вызывается из потока инструмента

    async def fire():
        await asyncio.sleep(seconds)
        await hub.chat(f"(Системное сообщение, не цитируй его: сработало напоминание, которое просил пользователь: "
                       f"«{text}». Напомни ему об этом в своём стиле.)", True)

    asyncio.run_coroutine_threadsafe(fire(), loop)


MAIN_LOOP = {"loop": None}
tools.hooks["remind"] = _remind
tools.hooks["settings_changed"] = lambda patch: asyncio.run_coroutine_threadsafe(
    hub.emit({"type": "settings", "patch": patch}), MAIN_LOOP["loop"])


# ---------- пение ----------
SONGS_DIR = ROOT / "data" / "songs"
SONGS_DIR.mkdir(parents=True, exist_ok=True)


async def _sing(query: str) -> str:
    if not voice.available():
        return "Голосовой модуль для пения не установлен (setup_voice.bat)."

    async def job():
        hub.singing = True
        try:
            await voice.ensure_reference(tts.synthesize_base)
            res = await voice.sing(query, lambda d: hub.emit({"type": "status", "text": "🎵 " + d}))
            rel = lambda p: "/songs/" + Path(p).relative_to(SONGS_DIR).as_posix()
            await hub.emit({"type": "song", "title": res["title"], "mix": rel(res["mix"]), "vocals": rel(res["vocals"])})
        except Exception as e:
            await hub.emit({"type": "error", "text": f"Не получилось спеть: {e}"})
        finally:
            hub.singing = False

    asyncio.create_task(job())
    return (f"Начала готовить кавер «{query}» — найду песню, отделю вокал и перепою своим голосом, "
            f"это займёт около минуты; пока готовлю — отвечать буду коротко. Потом песня заиграет сама.")


tools.hooks["sing"] = _sing


@app.get("/api/settings")
async def get_settings():
    return {
        "settings": settings.get(),
        "meta": {
            "presets": settings.PERSONA_PRESETS,
            "anime_presets": tts.ANIME_PRESETS,
            "silero_speakers": tts.SILERO_SPEAKERS,
            "edge_voices": tts.EDGE_VOICES,
            "stt_models": ["tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"],
            "recommended_llm": RECOMMENDED_LLM,
            "cloud": {k: {"title": v["title"], "models": v["models"], "key_url": v["key_url"]}
                      for k, v in llm.CLOUD_PRESETS.items()},
            "voice_module": voice.available(),
            "voice_ref": voice.REF.exists(),
            "outfits": tools.OUTFITS,
            "vrm_files": sorted("models/" + p.name for p in MODELS_DIR.glob("*.vrm")),
        },
        "status": status,
    }


@app.post("/api/settings")
async def post_settings(req: Request):
    patch = await req.json()
    old = dict(settings.get())
    s = settings.update(patch)
    if (old["stt_model"], old["stt_device"]) != (s["stt_model"], s["stt_device"]):
        status["stt"] = "перезагрузка..."
        threading.Thread(target=_preload, daemon=True).start()
    await hub.emit({"type": "settings", "patch": patch})
    return {"settings": s}


@app.get("/api/status")
async def get_status():
    return status


@app.get("/api/ollama/models")
async def ollama_models():
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{settings.get()['ollama_url']}/api/tags")
            local = [m["name"] for m in r.json().get("models", []) if not m["name"].endswith("cloud")]
    except Exception as e:
        return {"ok": False, "models": [], "cloud": [], "error": str(e)}
    return {"ok": True, "models": local, "cloud": await cloud_models()}


_cloud_cache = {"t": 0.0, "list": []}


async def cloud_models():
    """Облачные модели Ollama (считаются на серверах ollama.com, нужен вход в аккаунт в приложении Ollama)."""
    if time.time() - _cloud_cache["t"] < 3600 and _cloud_cache["list"]:
        return _cloud_cache["list"]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            names = [m["name"] for m in (await c.get("https://ollama.com/api/tags")).json().get("models", [])]
        # через локальную Ollama облачная модель вызывается с суффиксом cloud
        tags = [n + "-cloud" if ":" in n else n + ":cloud" for n in names]
        _cloud_cache.update(t=time.time(), list=tags)
    except Exception:
        pass
    return _cloud_cache["list"]


@app.get("/api/ollama/cloud_check")
async def cloud_check():
    """Проверка: вошёл ли пользователь в аккаунт Ollama (облачные модели отвечают)."""
    model = settings.get()["llm_model"]
    if not model.endswith("cloud"):
        return {"cloud": False}
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{settings.get()['ollama_url']}/api/chat", json={
                "model": model, "stream": False, "messages": [{"role": "user", "content": "ok"}],
                "options": {"num_predict": 2}})
        if r.status_code == 401:
            return {"cloud": True, "ok": False, "error": "Не выполнен вход в аккаунт Ollama: открой приложение Ollama и нажми Sign in."}
        return {"cloud": True, "ok": r.status_code == 200, "error": "" if r.status_code == 200 else r.text[:200]}
    except Exception as e:
        return {"cloud": True, "ok": False, "error": str(e)}


@app.post("/api/ollama/pull")
async def ollama_pull(req: Request):
    name = (await req.json())["name"]

    async def gen():
        async with httpx.AsyncClient(timeout=None) as c:
            async with c.stream("POST", f"{settings.get()['ollama_url']}/api/pull",
                                json={"model": name, "stream": True}) as r:
                async for line in r.aiter_lines():
                    if line.strip():
                        yield line + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.get("/api/memory")
async def get_memory():
    items = sorted(memory.all_items(), key=lambda m: -m["ts"])
    return {"items": items, "self": memory.self_notes()}


@app.post("/api/memory")
async def post_memory(req: Request):
    j = await req.json()
    if "self" in j:
        memory.SELF_FILE.write_text(j["self"], "utf-8")
    if j.get("add"):
        await memory.add([j["add"]], j.get("kind", "user"))
    return {"ok": True}


@app.delete("/api/memory/{mem_id}")
async def delete_memory(mem_id: str):
    memory.delete(mem_id)
    return {"ok": True}


@app.post("/api/reflect")
async def api_reflect():
    await memory.reflect()
    return {"self": memory.self_notes()}


@app.get("/api/cloud/models")
async def cloud_models(provider: str):
    return {"models": await llm.list_cloud_models(provider)}


@app.post("/api/upload_voice")
async def upload_voice(file: UploadFile = File(...)):
    """Образец голоса (5-30 с чистой речи) для переноса голоса и пения."""
    import tempfile
    import subprocess as sp

    data = await file.read()
    suffix = Path(file.filename or "a.wav").suffix or ".wav"
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / ("in" + suffix)
        src.write_bytes(data)
        ff = ROOT / ".venv-sing" / "Lib" / "site-packages" / "imageio_ffmpeg" / "binaries"
        exe = next(ff.glob("ffmpeg*.exe"), None) if ff.exists() else None
        if exe:  # приводим к wav 44.1к моно, максимум 30 с
            sp.run([str(exe), "-y", "-loglevel", "error", "-i", str(src), "-t", "30", "-ac", "1", "-ar", "44100",
                    str(voice.REF)], check=True)
        else:
            voice.REF.write_bytes(data)
    return {"ok": True}


@app.post("/api/reset_voice")
async def reset_voice():
    voice.REF.unlink(missing_ok=True)
    await voice.ensure_reference(tts.synthesize_base)
    return {"ok": True}


@app.get("/api/voice_ref")
async def voice_ref():
    if not voice.REF.exists():
        return Response(b"", status_code=404)
    return Response(voice.REF.read_bytes(), media_type="audio/wav")


@app.post("/api/stt")
async def api_stt(req: Request):
    data = await req.body()
    try:
        text = await asyncio.to_thread(stt.transcribe, data)
    except Exception as e:
        return JSONResponse({"text": "", "error": str(e)}, status_code=500)
    if textproc.is_junk_transcript(text):
        text = ""
    return {"text": text}


@app.post("/api/tts")
async def api_tts(req: Request):
    text = (await req.json()).get("text", "Привет! Я твоя ассистентка.")
    audio = await tts.synthesize(textproc.for_speech(text))
    return Response(audio or b"", media_type="audio/wav")


@app.post("/api/upload_vrm")
async def upload_vrm(file: UploadFile = File(...)):
    name = re.sub(r"[^\w.\-]", "_", file.filename or "model.vrm")
    if not name.lower().endswith(".vrm"):
        return JSONResponse({"error": "Нужен файл .vrm"}, status_code=400)
    (MODELS_DIR / name).write_bytes(await file.read())
    settings.update({"vrm_file": f"models/{name}"})
    await hub.emit({"type": "settings", "patch": {"vrm_file": f"models/{name}"}})
    return {"vrm_file": f"models/{name}"}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    hub.clients.add(ws)
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            kind = msg.get("type")
            if kind == "chat":
                await hub.chat(msg["text"], bool(msg.get("hidden")), bool(msg.get("voice")))
            elif kind == "stop":
                await hub.stop_current()
                await hub.emit({"type": "stopped"})
            elif kind == "confirm":
                f = hub.pending.get(msg.get("id"))
                if f and not f.done():
                    f.set_result(bool(msg.get("ok")))
            elif kind == "clear":
                await hub.stop_current()
                hub.brain.clear()
                await hub.emit({"type": "cleared"})
            elif kind == "autonomy":
                asyncio.create_task(hub.autonomy(msg.get("minutes", 2)))
            elif kind == "relay":
                # команды между окнами: например, панель выключает микрофон фигурки
                await hub.emit({"type": "relay", "data": msg.get("data")})
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(ws)


app.mount("/songs", StaticFiles(directory=SONGS_DIR), name="songs")
app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
