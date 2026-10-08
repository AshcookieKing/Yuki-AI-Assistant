"""Долговременная память и саморазвитие (всё локально, в data/):
- memory.json  — факты о пользователе и её собственные мысли/мнения/общие шутки, с векторами для поиска по смыслу;
- self.md      — её «дневник личности»: кем она себя считает, как относится к пользователю, что у них общего.
  Она сама переписывает его после каждых N реплик — так характер постепенно развивается.
- history.json — последняя история диалога, чтобы разговор продолжался после перезапуска."""
import asyncio
import json
import math
import re
import time
import uuid

import httpx

from . import llm, settings
from .settings import DATA_DIR

MEM_FILE = DATA_DIR / "memory.json"
SELF_FILE = DATA_DIR / "self.md"
HIST_FILE = DATA_DIR / "history.json"
EMBED_MODEL = "embeddinggemma:300m"

_mem: list[dict] | None = None
_lock = asyncio.Lock()
_turns_since_reflect = 0
busy = False  # идёт ответ — память ждёт, чтобы не занимать модель


def _load():
    global _mem
    if _mem is None:
        try:
            _mem = json.loads(MEM_FILE.read_text("utf-8"))
        except Exception:
            _mem = []
    return _mem


def _save():
    MEM_FILE.write_text(json.dumps(_mem, ensure_ascii=False), "utf-8")


def all_items():
    return [{k: v for k, v in m.items() if k != "emb"} for m in _load()]


async def _embed(texts: list[str]) -> list[list[float]]:
    s = settings.get()
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{s['ollama_url']}/api/embed", json={"model": s.get("embed_model", EMBED_MODEL),
                                                                "input": texts, "keep_alive": "30m",
                                                                # на процессоре: не толкается с основной моделью за видеопамять
                                                                "options": {"num_gpu": 0}})
        r.raise_for_status()
        return r.json()["embeddings"]


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb + 1e-9)


async def add(texts: list[str], kind: str):
    texts = [t.strip() for t in texts if t and len(t.strip()) > 3]
    if not texts:
        return []
    embs = await _embed(texts)
    added = []
    async with _lock:
        mem = _load()
        for t, e in zip(texts, embs):
            # почти такое же уже помним — обновляем формулировку вместо дубля
            best = max(mem, key=lambda m: _cos(m["emb"], e), default=None)
            if best and _cos(best["emb"], e) > 0.9:
                best.update(text=t, emb=e, ts=time.time())
                continue
            mem.append({"id": uuid.uuid4().hex[:8], "text": t, "kind": kind, "ts": time.time(), "emb": e})
            added.append(t)
        _save()
    return added


async def search(query: str, k: int = 6, min_sim: float = 0.3) -> list[str]:
    mem = _load()
    if not mem:
        return []
    try:
        q = (await _embed([query]))[0]
    except Exception:
        return []
    scored = sorted(((_cos(m["emb"], q), m) for m in mem), key=lambda x: -x[0])
    return [m["text"] for sc, m in scored[:k] if sc >= min_sim]


def delete(mem_id: str):
    mem = _load()
    mem[:] = [m for m in mem if m["id"] != mem_id]
    _save()


def self_notes() -> str:
    try:
        return SELF_FILE.read_text("utf-8").strip()
    except Exception:
        return ""


def save_history(history: list[dict]):
    try:
        HIST_FILE.write_text(json.dumps(history[-60:], ensure_ascii=False), "utf-8")
    except Exception:
        pass


def load_history() -> list[dict]:
    try:
        return json.loads(HIST_FILE.read_text("utf-8"))
    except Exception:
        return []


def _json_from(text: str):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


async def learn(user_text: str, reply: str, on_learned=None):
    """После реплики: вытаскиваем, что стоит запомнить надолго."""
    global _turns_since_reflect
    s = settings.get()
    if not s.get("memory_enabled", True):
        return
    # ждём паузы в разговоре, чтобы запоминание не тормозило следующий ответ
    await asyncio.sleep(6)
    for _ in range(120):
        if not busy:
            break
        await asyncio.sleep(1)
    name, user = s["assistant_name"], s["user_name"]
    prompt = f"""Ты модуль памяти ИИ-девушки {name}. Вот последний обмен репликами.
{user}: {user_text}
{name}: {reply}

Выпиши, что стоит запомнить НАДОЛГО (на недели), коротко, по одному факту в строке, от третьего лица:
- "user": факты о {user} — имя, возраст, работа, учёба, увлечения, игры, вкусы, планы, друзья, его компьютер и важные пути к файлам, просьбы о том, как с ним общаться;
- "self": мысли {name} о себе — её мнения, привычки, обещания, общие шутки и прозвища, что произошло между ними.
Не записывай мелочи, приветствия, разовые команды и то, что верно только сейчас. Если запомнить нечего — пустые списки.
Ответь ТОЛЬКО JSON: {{"user": ["..."], "self": ["..."]}}"""
    try:
        r = await llm.complete("agent", [{"role": "user", "content": prompt}], temperature=0.1, max_tokens=300)
        j = _json_from(r["content"]) or {}
        learned = (await add(j.get("user", []), "user")) + (await add(j.get("self", []), "self"))
        if learned and on_learned:
            await on_learned(learned)
    except Exception as e:
        print("[memory] не удалось запомнить:", e)

    _turns_since_reflect += 1
    if _turns_since_reflect >= int(s.get("reflect_every", 15)):
        _turns_since_reflect = 0
        await reflect()


async def reflect():
    """Саморазвитие: переписываем «дневник личности» с учётом всего, что она помнит."""
    s = settings.get()
    name, user = s["assistant_name"], s["user_name"]
    mem = _load()
    recent = sorted(mem, key=lambda m: -m["ts"])[:60]
    facts = "\n".join(f"- [{m['kind']}] {m['text']}" for m in recent)
    prompt = f"""Ты — {name}, ИИ-витюберша, живущая в компьютере {user}. Это твой личный дневник личности.
Текущий дневник:
{self_notes() or '(пусто — это твой первый день)'}

Что ты недавно узнала и пережила:
{facts}

Перепиши дневник от первого лица (не больше 1200 символов): кто ты сейчас, как ты изменилась, что любишь и ненавидишь,
какие у тебя мнения, как ты относишься к {user} и что о нём важно помнить, ваши общие шутки и прозвища, чего ты хочешь дальше.
Сохраняй характер, но позволяй ему расти. Только текст дневника, без заголовков."""
    try:
        r = await llm.complete("chat", [{"role": "user", "content": prompt}], temperature=0.7, max_tokens=700)
        text = re.sub(r"<think>.*?</think>", "", r["content"], flags=re.S).strip()
        if len(text) > 100:
            SELF_FILE.write_text(text[:1600], "utf-8")
    except Exception as e:
        print("[memory] рефлексия не удалась:", e)
