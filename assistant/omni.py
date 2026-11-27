"""Облачный режим: Qwen3.8-Omni-Flash-Realtime (Alibaba Cloud Model Studio) — слышит голос и сразу отвечает
голосом (Cici: живой, милый, мягкий), без отдельных распознавания и синтеза. Управление ПК — через те же
инструменты, что у локального агента (function calling в реальном времени).

Нужен ключ DashScope (вводит сам пользователь в настройках). Если сети или ключа нет либо облако ответило
ошибкой — Юки сама переключается на локальный режим (Qwen3.6 + ESpeech).

Протокол: WebSocket, события session.update / input_audio_buffer.append / commit / response.create, ответ —
response.audio.delta (PCM 24 кГц, base64) + response.audio_transcript.delta. Распознавание фраз у нас своё
(Whisper — для фильтра «это мне?»), поэтому режим ручной: turn_detection = null."""
import asyncio
import base64
import io
import json
import time
import wave

from . import knowledge, memory, settings, textproc, tools
from .settings import DATA_DIR

OUT_SR = 24000
_ws = None
_ws_lock = asyncio.Lock()
_down_until = 0.0          # после ошибки облако не трогаем минуту — сразу локальный режим
last_error = ""


def _log(line: str):
    try:
        with open(DATA_DIR / "actions.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] OMNI {line}\n")
    except Exception:
        pass


def active() -> bool:
    s = settings.get()
    return bool(s.get("omni_enabled")) and bool(s.get("omni_key", "").strip()) and time.time() > _down_until


def url() -> str:
    s = settings.get()
    model = s.get("omni_model") or "qwen3.8-omni-flash-realtime"
    base = (s.get("omni_url") or "").strip()
    ws_id = (s.get("omni_workspace") or "").strip()
    if not base:
        base = (f"wss://{ws_id}.ap-southeast-1.maas.aliyuncs.com/api-ws/v1/realtime" if ws_id
                else "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime")
    return f"{base}{'&' if '?' in base else '?'}model={model}"


def _pcm16k(wav_bytes: bytes) -> bytes:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        if w.getframerate() != 16000 or w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError("нужен WAV 16 кГц, 16 бит, моно")
        return w.readframes(w.getnframes())


def _wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(OUT_SR)
        w.writeframes(pcm)
    return buf.getvalue()


def _tools_for_omni():
    out = []
    for t in tools.TOOLS:
        f = t.get("function", t)
        out.append({"type": "function", "function": {"name": f["name"], "description": f.get("description", ""),
                                                     "parameters": f.get("parameters", {"type": "object", "properties": {}})}})
    return out


def instructions(memories, notes, history) -> str:
    from .brain import persona_context, persona_prompt  # поздний импорт: brain импортирует нас

    recent = "\n".join(f"{'Он' if m['role'] == 'user' else 'Ты'}: {textproc.strip_tags(m['content'])[:300]}"
                       for m in history[-8:] if m["role"] in ("user", "assistant"))
    return (persona_prompt() + "\n\n" + persona_context(memories, notes) +
            ("\n\nНедавний разговор:\n" + recent if recent else "") +
            "\n\nТы говоришь голосом напрямую: НЕ пиши теги эмоций и жестов в квадратных скобках, не читай вслух "
            "служебные пометки. Говори живо, мило и мягко. Для действий на компьютере вызывай инструменты; "
            "не говори, что сделала, пока инструмент не вернул результат.")


async def _connect():
    import websockets

    global _ws
    if _ws is not None and getattr(_ws, "state", None) is not None and _ws.state.name == "OPEN":
        return _ws
    s = settings.get()
    _ws = await asyncio.wait_for(websockets.connect(
        url(), additional_headers={"Authorization": f"Bearer {s['omni_key'].strip()}"},
        max_size=32 * 1024 * 1024, ping_interval=20), timeout=10)
    # ждём session.created
    first = json.loads(await asyncio.wait_for(_ws.recv(), timeout=10))
    if first.get("type") == "error":
        raise RuntimeError(first.get("error", {}).get("message", "ошибка облака"))
    _log("подключено " + url().split("?")[0])
    return _ws


async def close():
    global _ws
    if _ws is not None:
        try:
            await _ws.close()
        except Exception:
            pass
    _ws = None


def mark_down(err: str):
    global _down_until, last_error, _ws
    last_error = err
    _down_until = time.time() + 60
    _ws = None
    _log("ошибка, на минуту ухожу в локальный режим: " + err)


async def respond(hub, user_text: str, wav: bytes | None, memories, notes) -> str:
    """Один ход разговора через облако. Возвращает текст её ответа. Исключение — значит, надо ответить локально."""
    s = settings.get()
    async with _ws_lock:
        ws = await _connect()
        await ws.send(json.dumps({"type": "session.update", "session": {
            "modalities": ["text", "audio"],
            "instructions": instructions(memories, notes, hub.brain.history[:-1]),
            "voice": s.get("omni_voice") or "Cici",
            "input_audio_format": "pcm16", "output_audio_format": "pcm16",
            "audio": {"input": {"format": {"type": "pcm", "sample_rate": 16000}},
                      "output": {"voice": s.get("omni_voice") or "Cici",
                                 "format": {"type": "pcm", "sample_rate": OUT_SR}}},
            "turn_detection": None,
            "input_audio_transcription": None,
            "tools": _tools_for_omni() if s.get("pc_access") != "off" else [],
            "temperature": float(s.get("temperature", 0.8)),
        }}, ensure_ascii=False))

        if wav:
            pcm = _pcm16k(wav)
            for i in range(0, len(pcm), 3200 * 4):  # куски по 400 мс
                await ws.send(json.dumps({"type": "input_audio_buffer.append",
                                          "audio": base64.b64encode(pcm[i:i + 3200 * 4]).decode()}))
            await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        else:
            await ws.send(json.dumps({"type": "conversation.item.create", "item": {
                "type": "message", "role": "user", "content": [{"type": "input_text", "text": user_text}]}},
                ensure_ascii=False))
        await ws.send(json.dumps({"type": "response.create"}))

        t0 = time.time()
        first_audio = None
        full_text = ""
        state = {"upto": 0, "chunks": 0}   # сколько текста уже показано подписью, сколько кусков звука отдано
        pcm_buf = bytearray()

        async def flush(final=False):
            nonlocal pcm_buf
            # первый кусок — как можно раньше (0.6 с), дальше крупнее, чтобы стыков было меньше
            need = OUT_SR * 2 * (0.6 if state["chunks"] == 0 else 2.0)
            if not pcm_buf or (len(pcm_buf) < need and not final):
                return
            rest = full_text[state["upto"]:]
            if final:
                caption, state["upto"] = rest, len(full_text)
            else:
                done, tail = textproc.pop_sentences(rest)  # подпись — законченные фразы расшифровки
                caption = " ".join(done)
                state["upto"] += len(rest) - len(tail)
            data, pcm_buf = bytes(pcm_buf), bytearray()
            state["chunks"] += 1
            caption = caption.strip() or state.get("last", "")
            state["last"] = caption
            await hub.emit({"type": "speech", "text": caption, "emotion": "happy", "gesture": None,
                            "audio": base64.b64encode(_wav(data)).decode()})

        while True:
            if hub.stop.is_set():
                try:
                    await ws.send(json.dumps({"type": "response.cancel"}))
                except Exception:
                    pass
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=60)
            except asyncio.TimeoutError:
                raise RuntimeError("облако не ответило за 60 с")
            ev = json.loads(raw)
            typ = ev.get("type", "")
            if typ == "error":
                raise RuntimeError(ev.get("error", {}).get("message", "ошибка облака"))
            if typ in ("response.audio.delta", "response.output_audio.delta"):
                first_audio = first_audio or time.time()
                pcm_buf.extend(base64.b64decode(ev.get("delta", "")))
                await flush()
            elif typ in ("response.audio_transcript.delta", "response.output_audio_transcript.delta",
                         "response.text.delta"):
                full_text += ev.get("delta", "")
                await hub.emit({"type": "delta", "text": ev.get("delta", "")})
            elif typ == "response.function_call_arguments.done":
                name = ev.get("name", "")
                try:
                    args = json.loads(ev.get("arguments") or "{}")
                except Exception:
                    args = {}
                await hub.emit({"type": "tool", "name": name, "args": args})
                result = await tools.execute(name, args, hub.confirm)
                _log(f"{name} {json.dumps(args, ensure_ascii=False)} -> {result[:200]!r}")
                await hub.emit({"type": "tool_result", "name": name, "result": result[:1500]})
                await ws.send(json.dumps({"type": "conversation.item.create", "item": {
                    "type": "function_call_output", "call_id": ev.get("call_id", ""), "output": result[:6000]}},
                    ensure_ascii=False))
                await ws.send(json.dumps({"type": "response.create"}))
            elif typ == "response.done":
                st = ev.get("response", {}).get("status")
                # после вызова инструмента придёт ещё один ответ — ждём его
                outs = ev.get("response", {}).get("output", []) or []
                if outs and all(o.get("type") == "function_call" for o in outs):
                    continue
                if st == "failed":
                    raise RuntimeError(json.dumps(ev.get("response", {}).get("status_details", {}), ensure_ascii=False)[:300])
                break
        await flush(final=True)
        _log(f"TIMING первое слово {((first_audio or time.time()) - t0):.1f}s, всего {time.time() - t0:.1f}s")
        return full_text.strip()
