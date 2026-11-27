"""Собирает набор для дообучения (LoRA) из хранилища Obsidian и истории разговоров.

Источники:
- «Юки/Примеры.md» в хранилище — образцы «Хозяин: … / Юки: …» (главный источник стиля);
- data/history.json — реальные реплики, где ты её не поправлял.
Результат: data/train.jsonl в формате чатов (messages), подходит для Unsloth / LLaMA-Factory / axolotl.

Запуск:  .venv\\Scripts\\python tools\\make_dataset.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from assistant import knowledge, settings, textproc  # noqa: E402
from assistant.settings import DATA_DIR  # noqa: E402

settings.load()
s = settings.get()
system = (s["persona"].replace("{name}", s["assistant_name"]).replace("{user}", s["user_name"]) +
          " Отвечай коротко, по-русски, начинай с тега эмоции.")
rows = []
for q, a in knowledge.examples():
    rows.append({"messages": [{"role": "system", "content": system},
                              {"role": "user", "content": q}, {"role": "assistant", "content": a}]})
try:
    hist = json.loads((DATA_DIR / "history.json").read_text("utf-8"))
except Exception:
    hist = []
for i in range(len(hist) - 1):
    u, b = hist[i], hist[i + 1]
    if u["role"] == "user" and b["role"] == "assistant" and 3 < len(b["content"]) < 400:
        rows.append({"messages": [{"role": "system", "content": system},
                                  {"role": "user", "content": u["content"]},
                                  {"role": "assistant", "content": b["content"]}]})
out = DATA_DIR / "train.jsonl"
with open(out, "w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
print(f"{len(rows)} примеров -> {out}")
