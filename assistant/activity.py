"""Фоновое изучение того, чем пользуется пользователь (всё локально, data/activity.json).
Раз в 30 с запоминаем активное окно и программу; раз в полчаса делаем выводы о привычках и кладём их в память.
Окна с банками, паролями, инкогнито и т.п. не записываются вовсе."""
import asyncio
import ctypes
import ctypes.wintypes
import json
import re
import time
from datetime import date, datetime

import psutil

from . import llm, memory, settings
from .settings import DATA_DIR

FILE = DATA_DIR / "activity.json"
TICK = 30
PRIVATE = re.compile(r"bank|банк|сбер|тинькофф|т-банк|альфа|втб|парол|password|bitwarden|keepass|1password|"
                     r"incognito|инкогнито|inprivate|private brows|приватн|госуслуг|wallet|кошел", re.I)
# хвосты заголовков браузеров и программ, которые не несут смысла
SUFFIX = re.compile(r"\s*[-—|]\s*(Google Chrome|Яндекс Браузер|Yandex|Mozilla Firefox|Microsoft​? Edge|Opera|Brave|"
                    r"Visual Studio Code|Discord|Telegram)\s*$", re.I)

_data: dict | None = None
_current = {"proc": "", "title": "", "since": time.time()}


def _load():
    global _data
    if _data is None:
        try:
            _data = json.loads(FILE.read_text("utf-8"))
        except Exception:
            _data = {}
    return _data


def _save():
    d = _load()
    # храним только последние 30 дней
    for k in sorted(d)[:-30]:
        d.pop(k, None)
    FILE.write_text(json.dumps(d, ensure_ascii=False), "utf-8")


def _foreground():
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    pid = ctypes.wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        proc = psutil.Process(pid.value).name().removesuffix(".exe")
    except Exception:
        proc = "?"
    return proc, buf.value.strip()


def _tick():
    proc, title = _foreground()
    if not title or proc.lower() in ("electron", "python"):  # пустые окна и сама фигурка не в счёт
        return
    if PRIVATE.search(title) or PRIVATE.search(proc):
        title = "(приватное окно)"
    title = SUFFIX.sub("", title)[:80]
    if proc != _current["proc"] or title != _current["title"]:
        _current.update(proc=proc, title=title, since=time.time())
    day = _load().setdefault(str(date.today()), {"apps": {}, "titles": {}})
    day["apps"][proc] = day["apps"].get(proc, 0) + TICK
    if title != "(приватное окно)":
        day["titles"][title] = day["titles"].get(title, 0) + TICK


def now_text() -> str:
    """Чем пользователь занят прямо сейчас — для её реплик и помощи."""
    if not _current["proc"]:
        return ""
    mins = int((time.time() - _current["since"]) / 60)
    return f"Сейчас он в «{_current['title'] or _current['proc']}» ({_current['proc']}), уже {mins} мин."


def summary(days: int = 1, top: int = 8) -> str:
    d = _load()
    apps, titles = {}, {}
    for k in sorted(d)[-days:]:
        for a, v in d[k]["apps"].items():
            apps[a] = apps.get(a, 0) + v
        for t, v in d[k]["titles"].items():
            titles[t] = titles.get(t, 0) + v
    fa = ", ".join(f"{a} {v // 60} мин" for a, v in sorted(apps.items(), key=lambda x: -x[1])[:top])
    ft = "; ".join(f"{t} ({v // 60} мин)" for t, v in sorted(titles.items(), key=lambda x: -x[1])[:top])
    return f"Программы: {fa}\nОкна/сайты: {ft}" if fa else ""


async def learn():
    """Выводы о привычках → в долговременную память."""
    stats = summary(days=1, top=12)
    if not stats:
        return
    user = settings.get()["user_name"]
    prompt = (f"Это статистика, чем {user} пользовался на компьютере сегодня:\n{stats}\n\n"
              f"Сделай 1-3 полезных вывода о его привычках, интересах, работе и играх (от третьего лица, кратко), "
              "которые помогут ассистенту лучше ему помогать. Не перечисляй минуты. "
              'Ответь ТОЛЬКО JSON: {"facts": ["..."]}')
    try:
        r = await llm.complete("agent", [{"role": "user", "content": prompt}], temperature=0.2, max_tokens=250)
        m = re.search(r"\{.*\}", r["content"], re.S)
        facts = json.loads(m.group(0)).get("facts", []) if m else []
        if facts:
            await memory.add(facts, "user")
    except Exception as e:
        print("[activity] не удалось сделать выводы:", e)


async def run():
    last_learn = time.time()
    while True:
        await asyncio.sleep(TICK)
        if not settings.get().get("learn_activity", True):
            continue
        try:
            await asyncio.to_thread(_tick)
            _save()
        except Exception as e:
            print("[activity]", e)
        if time.time() - last_learn > 1800 and not memory.busy:
            last_learn = time.time()
            await learn()
