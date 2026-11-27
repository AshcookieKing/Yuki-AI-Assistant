"""База знаний из хранилища Obsidian.

Юки «учится» на заметках: все .md из папки хранилища режутся на куски по заголовкам, получают векторы
(embeddinggemma на процессоре) и хранятся в data/knowledge.npz. На каждую реплику берутся самые близкие
по смыслу куски и подкладываются модели — так знания из заметок работают без интернета и без переобучения,
а новые заметки подхватываются сами (индекс обновляется по времени изменения файлов).

Особые заметки (папка «Юки» внутри хранилища):
- «Правила.md»  — как ей себя вести и отвечать (всегда добавляется целиком в начало промпта агента и персонажа);
- «Примеры.md»  — образцы диалогов «Хозяин: … / Юки: …» (из них же собирается набор для дообучения LoRA).
"""
import asyncio
import json
import re
import time
from pathlib import Path

import numpy as np

from . import memory, settings
from .settings import DATA_DIR

INDEX = DATA_DIR / "knowledge.npz"
META = DATA_DIR / "knowledge.json"
DEFAULT_VAULT = DATA_DIR / "vault"
SKIP_DIRS = {".obsidian", ".trash", ".git", "node_modules"}
CHUNK = 900  # символов в куске

_lock = asyncio.Lock()
_meta: list[dict] = []
_emb: np.ndarray | None = None
_files: dict[str, float] = {}
_last_scan = 0.0


def vault_dir() -> Path:
    p = (settings.get().get("obsidian_vault") or "").strip()
    return Path(p) if p else DEFAULT_VAULT


def ensure_vault():
    """Создаёт стартовое хранилище, если своего у пользователя нет (его можно открыть в Obsidian как хранилище)."""
    v = vault_dir()
    if v != DEFAULT_VAULT or (v / "Юки").exists():
        return
    (v / "Юки").mkdir(parents=True, exist_ok=True)
    (v / "Юки" / "Правила.md").write_text(
        "# Правила Юки\n\nПиши сюда, как ей отвечать и что важно. Эта заметка всегда у неё в голове.\n\n"
        "- Отвечай коротко и по делу, 1–2 предложения.\n"
        "- Если не знаешь — честно скажи, не выдумывай.\n", "utf-8")
    (v / "Юки" / "Примеры.md").write_text(
        "# Примеры диалогов\n\nОбразцы того, как она должна отвечать. Формат: строка «Хозяин:», затем «Юки:».\n\n"
        "Хозяин: Привет, как дела?\nЮки: [smug] Лучше, чем у тебя, раз ты опять ко мне пришёл. Что надо?\n", "utf-8")
    (v / "Знания").mkdir(exist_ok=True)
    (v / "Знания" / "Обо мне.md").write_text(
        "# Обо мне\n\nФакты о себе, проектах, друзьях, играх — всё, что Юки должна знать.\n", "utf-8")


def _scan() -> dict[str, float]:
    v = vault_dir()
    out = {}
    if not v.exists():
        return out
    for p in v.rglob("*.md"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        try:
            out[str(p)] = p.stat().st_mtime
        except OSError:
            pass
    return out


def _strip_md(text: str) -> str:
    text = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)        # frontmatter
    text = re.sub(r"!\[\[[^\]]*\]\]|!\[[^\]]*\]\([^)]*\)", "", text)  # картинки
    text = re.sub(r"\[\[([^\]|]*\|)?([^\]]*)\]\]", r"\2", text)       # вики-ссылки
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)             # ссылки
    text = re.sub(r"```.*?```", lambda m: m.group(0)[:600], text, flags=re.S)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _chunks(path: str) -> list[str]:
    try:
        raw = Path(path).read_text("utf-8", errors="ignore")
    except OSError:
        return []
    title = Path(path).stem
    text = _strip_md(raw)
    parts, cur, head = [], "", title
    for line in text.splitlines():
        if line.startswith("#"):
            if cur.strip():
                parts.append((head, cur))
            head, cur = f"{title} › {line.lstrip('#').strip()}", ""
            continue
        cur += line + "\n"
        if len(cur) > CHUNK:
            parts.append((head, cur))
            cur = ""
    if cur.strip():
        parts.append((head, cur))
    return [f"[{h}] {c.strip()}" for h, c in parts if len(c.strip()) > 20]


def _load_index():
    global _meta, _emb, _files
    if _emb is not None:
        return
    try:
        d = json.loads(META.read_text("utf-8"))
        _meta, _files = d["chunks"], d["files"]
        _emb = np.load(INDEX)["emb"] if _meta else np.zeros((0, 1), np.float32)
    except Exception:
        _meta, _files, _emb = [], {}, np.zeros((0, 1), np.float32)


def _save_index():
    META.write_text(json.dumps({"chunks": _meta, "files": _files}, ensure_ascii=False), "utf-8")
    np.savez_compressed(INDEX, emb=_emb)


async def refresh(force: bool = False) -> dict:
    """Переиндексирует изменённые заметки. Возвращает {'files', 'chunks', 'updated'}."""
    global _meta, _emb, _files, _last_scan
    async with _lock:
        _load_index()
        if not force and time.time() - _last_scan < 60:
            return {"files": len(_files), "chunks": len(_meta), "updated": 0}
        _last_scan = time.time()
        ensure_vault()
        now = await asyncio.to_thread(_scan)
        changed = [p for p, m in now.items() if force or _files.get(p) != m]
        gone = set(_files) - set(now)
        if not changed and not gone:
            return {"files": len(now), "chunks": len(_meta), "updated": 0}
        drop = set(changed) | gone
        keep = [i for i, c in enumerate(_meta) if c["file"] not in drop]
        meta = [_meta[i] for i in keep]
        emb = _emb[keep] if len(keep) and _emb.shape[0] else None
        new_meta, new_texts = [], []
        for p in changed:
            for c in await asyncio.to_thread(_chunks, p):
                new_meta.append({"file": p, "text": c})
                new_texts.append(c)
        vecs = []
        for i in range(0, len(new_texts), 32):
            vecs += await memory._embed(new_texts[i:i + 32])
        if vecs:
            v = np.asarray(vecs, np.float32)
            v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-9
            emb = v if emb is None else np.vstack([emb, v])
            meta += new_meta
        _meta = meta
        _emb = emb if emb is not None else np.zeros((0, 1), np.float32)
        _files = now
        await asyncio.to_thread(_save_index)
        return {"files": len(now), "chunks": len(_meta), "updated": len(changed)}


async def search(query: str, k: int = 4, min_sim: float = 0.35) -> list[str]:
    if not settings.get().get("knowledge_enabled", True):
        return []
    try:
        await refresh()
        if not _meta:
            return []
        q = np.asarray((await memory._embed([query]))[0], np.float32)
        q /= np.linalg.norm(q) + 1e-9
        sims = _emb @ q
        idx = np.argsort(-sims)[:k]
        return [_meta[i]["text"][:1200] for i in idx if sims[i] >= min_sim]
    except Exception as e:
        print("[KNOW] поиск по заметкам не удался:", e)
        return []


def rules() -> str:
    """Заметка «Юки/Правила.md» — всегда в промпте."""
    p = vault_dir() / "Юки" / "Правила.md"
    try:
        return _strip_md(p.read_text("utf-8"))[:3000]
    except OSError:
        return ""


def examples() -> list[tuple[str, str]]:
    """Пары (реплика, ответ) из «Юки/Примеры.md» — для few-shot и набора дообучения."""
    p = vault_dir() / "Юки" / "Примеры.md"
    try:
        text = p.read_text("utf-8")
    except OSError:
        return []
    user = settings.get().get("user_name", "Хозяин")
    name = settings.get().get("assistant_name", "Юки")
    pairs, q = [], None
    for line in text.splitlines():
        m = re.match(rf"\s*(?:{re.escape(user)}|Я|Пользователь)\s*:\s*(.+)", line)
        a = re.match(rf"\s*(?:{re.escape(name)}|Юки)\s*:\s*(.+)", line)
        if m:
            q = m.group(1).strip()
        elif a and q:
            pairs.append((q, a.group(1).strip()))
            q = None
    return pairs
