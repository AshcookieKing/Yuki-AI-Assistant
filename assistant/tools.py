"""Инструменты управления компьютером и получения информации, доступные модели."""
import asyncio
import base64
import html
import inspect
import io
import os
import platform
import re
import subprocess
import urllib.parse
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import psutil

from . import settings

MAX_OUT = 4000
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

# сервер подставляет сюда функции: напоминание, пение, зрение и поиск точки на экране
hooks = {"remind": None, "sing": None, "vision": None, "ground": None}

# координаты скриншота и мыши должны совпадать при любом масштабе Windows (125%, 150%...)
try:
    import ctypes

    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass


def _fn(name, desc, props=None, required=None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props or {}, "required": required or []},
        },
    }


def _s(desc):
    return {"type": "string", "description": desc}


def _i(desc):
    return {"type": "integer", "description": desc}


TOOLS = [
    _fn("run_command", "Выполнить команду PowerShell и получить вывод. Используй для всего, чего нет в других "
        "инструментах: настройки Windows, сеть, установка, работа с файлами массово и т.д.",
        {"command": _s("Команда PowerShell")}, ["command"]),
    _fn("open_app", "Открыть программу, файл или папку (notepad, calc, explorer, steam://open/main, "
        "путь к .exe или документу, ms-settings:display и т.п.).",
        {"target": _s("Имя программы, путь или URI")}, ["target"]),
    _fn("close_app", "Закрыть программу по имени процесса (chrome, notepad, Telegram).",
        {"process_name": _s("Имя процесса без .exe")}, ["process_name"]),
    _fn("open_url", "Открыть сайт в браузере.", {"url": _s("Адрес сайта")}, ["url"]),
    _fn("open_search", "Открыть в браузере страницу поиска Google или YouTube по запросу (чтобы пользователь сам увидел).",
        {"query": _s("Поисковый запрос"), "site": _s("google или youtube")}, ["query"]),
    _fn("play_youtube", "Найти на YouTube и сразу включить первое видео/песню по запросу.",
        {"query": _s("Что включить")}, ["query"]),
    _fn("web_search", "Найти информацию в интернете и получить текст результатов (новости, факты, цены, "
        "что угодно актуальное). Используй, когда нужны свежие данные или ты не уверена в ответе.",
        {"query": _s("Поисковый запрос")}, ["query"]),
    _fn("read_webpage", "Прочитать текст веб-страницы по адресу.", {"url": _s("Адрес")}, ["url"]),
    _fn("weather", "Погода сейчас и прогноз на 3 дня для города.", {"city": _s("Город")}, ["city"]),
    _fn("look_at_screen", "Сделать снимок экрана и посмотреть, что на нём (какие окна, текст, картинки, ошибки). "
        "Используй, когда просят посмотреть на экран, помочь с тем, что открыто, или прочитать что-то.",
        {"question": _s("Что именно нужно понять по экрану")}, ["question"]),
    _fn("list_windows", "Список открытых окон."),
    _fn("focus_window", "Переключиться на окно (развернуть и сделать активным) по части заголовка.",
        {"title": _s("Часть заголовка окна")}, ["title"]),
    _fn("minimize_window", "Свернуть окно по части заголовка, или все окна если title='all'.",
        {"title": _s("Часть заголовка или all")}, ["title"]),
    _fn("find_files", "Найти файлы по имени (маске) в папке и подпапках.",
        {"pattern": _s("Маска, например *.mp3 или *отчёт*"), "folder": _s("Где искать, по умолчанию домашняя папка")},
        ["pattern"]),
    _fn("list_directory", "Показать содержимое папки.", {"path": _s("Путь к папке")}, ["path"]),
    _fn("read_file", "Прочитать текстовый файл.", {"path": _s("Путь к файлу")}, ["path"]),
    _fn("write_file", "Создать или перезаписать текстовый файл.",
        {"path": _s("Путь к файлу"), "content": _s("Содержимое")}, ["path", "content"]),
    _fn("system_info", "Загрузка процессора, свободная память, диски, батарея, время работы."),
    _fn("list_processes", "Список запущенных процессов по потреблению памяти.",
        {"filter": _s("Необязательный фильтр по имени")}),
    _fn("set_volume", "Установить громкость системы 0-100.", {"percent": _i("0-100")}, ["percent"]),
    _fn("media_control", "Управление музыкой/видео: playpause, next, prev, mute.",
        {"action": _s("playpause | next | prev | mute")}, ["action"]),
    _fn("click_on", "Найти на экране элемент по описанию (кнопка, ссылка, поле, значок, пункт меню, вкладка) и "
        "кликнуть по нему мышкой. Описывай подробно: «кнопка Отправить внизу чата», «поле поиска на YouTube».",
        {"target": _s("Что найти и кликнуть"), "action": _s("click | double | right | move (только навести)")},
        ["target"]),
    _fn("mouse_click_xy", "Кликнуть мышкой по точным координатам экрана в пикселях.",
        {"x": _i("X"), "y": _i("Y"), "button": _s("left | right | middle"), "double": {"type": "boolean"}}, ["x", "y"]),
    _fn("drag", "Перетащить мышкой что-то из одного места экрана в другое (по описаниям).",
        {"from_target": _s("Что взять"), "to_target": _s("Куда перетащить")}, ["from_target", "to_target"]),
    _fn("scroll", "Прокрутить колесом мыши в активном окне.",
        {"direction": _s("up | down"), "amount": _i("Сколько щелчков (по умолчанию 5)")}, ["direction"]),
    _fn("ui_elements", "Список кнопок, полей и пунктов активного окна (по данным Windows) с названиями и координатами — "
        "точнее зрения для обычных программ. Потом можно click_element."),
    _fn("click_element", "Кликнуть элемент активного окна по его названию из ui_elements.",
        {"name": _s("Название элемента")}, ["name"]),
    _fn("wait", "Подождать несколько секунд (пока загрузится окно или страница).", {"seconds": {"type": "number"}},
        ["seconds"]),
    _fn("type_text", "Напечатать текст туда, где стоит курсор (сначала кликни в нужное поле).", {"text": _s("Текст")},
        ["text"]),
    _fn("press_keys", "Нажать сочетание клавиш: 'ctrl+c', 'alt+tab', 'win+d', 'enter', 'f5'.",
        {"keys": _s("Сочетание через +")}, ["keys"]),
    _fn("clipboard", "Прочитать или записать буфер обмена.",
        {"action": _s("get | set"), "text": _s("Текст для set")}, ["action"]),
    _fn("set_outfit", "Переодеться (сменить наряд своей 3D-модели).",
        {"name": _s("Наряд: Обычная, Неко, Ведьмочка, Ангел, Демонесса, Киберпанк, Школьница, Зимняя")}, ["name"]),
    _fn("set_reminder", "Поставить напоминание или таймер: через сколько минут что сказать.",
        {"minutes": {"type": "number", "description": "Через сколько минут"}, "text": _s("О чём напомнить")},
        ["minutes", "text"]),
    _fn("sing_song", "Спеть песню своим голосом (кавер): найти песню на YouTube, отделить вокал и перепеть. "
        "Занимает 1-3 минуты.", {"query": _s("Название песни и исполнитель")}, ["query"]),
]

# Эти действия в режиме «с подтверждением» требуют нажатия «Разрешить»
NEEDS_CONFIRM = {"run_command", "write_file", "close_app", "type_text", "press_keys", "click_on", "mouse_click_xy",
                 "drag", "click_element"}
# В режиме «полный доступ» всё равно спрашиваем перед необратимым (можно отключить в настройках)
DESTRUCTIVE_RE = re.compile(
    r"remove-item|\brm\b|\bdel\b|\berase\b|rmdir|\brd\b|format-volume|\bformat\b|diskpart|clear-disk|"
    r"reg\s+delete|remove-itemproperty|stop-computer|restart-computer|shutdown|bcdedit|cipher\s+/w|"
    r"uninstall|remove-appxpackage|set-mppreference", re.I)


def _clip(text: str, n: int = MAX_OUT) -> str:
    return text if len(text) <= n else text[:n] + "\n...[обрезано]"


def _path(p: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(p.strip().strip('"'))))


def run_command(command: str) -> str:
    timeout = int(settings.get().get("command_timeout", 60))
    full = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; " + command
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", full],
            capture_output=True, timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        out = r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")
        return _clip(f"код выхода {r.returncode}\n{out.strip() or '(нет вывода)'}")
    except subprocess.TimeoutExpired:
        return f"Команда не завершилась за {timeout} с."


def open_app(target: str) -> str:
    # модели часто пишут $env:USERPROFILE или %USERPROFILE% — раскрываем оба
    t = re.sub(r"\$env:(\w+)", lambda m: os.environ.get(m.group(1), m.group(0)), target.strip())
    t = os.path.expandvars(t)
    try:
        os.startfile(t.strip('"'))
    except OSError:
        # «программа аргументы» (explorer "C:\папка") — запускаем через cmd start
        r = subprocess.run(f'start "" {t}', shell=True, capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode != 0:
            return f"Не удалось открыть {t}: {r.stderr.decode('cp866', 'replace')[:300]}"
    return f"Запущено: {t}"


def close_app(process_name: str) -> str:
    name = process_name.lower().removesuffix(".exe")
    n = 0
    for p in psutil.process_iter(["name"]):
        if (p.info["name"] or "").lower().removesuffix(".exe") == name:
            try:
                p.terminate()
                n += 1
            except Exception:
                pass
    return f"Закрыто процессов: {n}" if n else f"Процесс {process_name} не найден"


def open_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    return f"Открыто: {url}"


def open_search(query: str, site: str = "google") -> str:
    q = urllib.parse.quote_plus(query)
    url = (f"https://www.youtube.com/results?search_query={q}" if "you" in (site or "").lower()
           else f"https://www.google.com/search?q={q}")
    webbrowser.open(url)
    return f"Открыт поиск: {query}"


def play_youtube(query: str) -> str:
    r = httpx.get("https://www.youtube.com/results", params={"search_query": query},
                  headers={"User-Agent": UA, "Accept-Language": "ru"}, timeout=15)
    ids = re.findall(r'"videoId":"([\w-]{11})"', r.text)
    if not ids:
        return open_search(query, "youtube")
    m = re.search(r'"title":\{"runs":\[\{"text":"([^"]+)"', r.text)
    webbrowser.open(f"https://www.youtube.com/watch?v={ids[0]}")
    return f"Включено видео: {m.group(1) if m else ids[0]}"


def web_search(query: str) -> str:
    key = settings.get().get("tavily_key", "")
    if not key:
        return ("Веб-поиск не настроен (нужен бесплатный ключ Tavily в настройках). "
                "Ответь из своих знаний или открой поиск в браузере через open_search.")
    r = httpx.post("https://api.tavily.com/search", timeout=30, json={
        "api_key": key, "query": query, "max_results": 5, "include_answer": True, "search_depth": "basic"})
    j = r.json()
    if "results" not in j:
        return f"Ошибка поиска: {j}"
    parts = [f"Краткий ответ: {j['answer']}"] if j.get("answer") else []
    for it in j["results"]:
        parts.append(f"- {it['title']} ({it['url']}): {it.get('content', '')[:500]}")
    return _clip("\n".join(parts))


def _html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style|noscript|svg|head).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def read_webpage(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    r = httpx.get(url, headers={"User-Agent": UA, "Accept-Language": "ru,en"}, timeout=20, follow_redirects=True)
    return _clip(_html_to_text(r.text), 6000)


WMO = {0: "ясно", 1: "в основном ясно", 2: "переменная облачность", 3: "пасмурно", 45: "туман", 48: "изморозь",
       51: "морось", 53: "морось", 55: "сильная морось", 61: "небольшой дождь", 63: "дождь", 65: "ливень",
       71: "небольшой снег", 73: "снег", 75: "сильный снег", 77: "снежная крупа", 80: "ливни", 81: "ливни",
       82: "сильные ливни", 85: "снегопад", 86: "сильный снегопад", 95: "гроза", 96: "гроза с градом", 99: "гроза с градом"}


def weather(city: str) -> str:
    g = httpx.get("https://geocoding-api.open-meteo.com/v1/search",
                  params={"name": city, "count": 1, "language": "ru"}, timeout=15).json()
    if not g.get("results"):
        return f"Город {city} не найден"
    p = g["results"][0]
    w = httpx.get("https://api.open-meteo.com/v1/forecast", timeout=15, params={
        "latitude": p["latitude"], "longitude": p["longitude"], "timezone": "auto", "forecast_days": 3,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
        "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max"}).json()
    c = w["current"]
    lines = [f"{p['name']}: сейчас {c['temperature_2m']}°C (ощущается {c['apparent_temperature']}°C), "
             f"{WMO.get(c['weather_code'], '')}, ветер {c['wind_speed_10m']} км/ч, влажность {c['relative_humidity_2m']}%"]
    d = w["daily"]
    for i, day in enumerate(d["time"]):
        lines.append(f"{day}: {d['temperature_2m_min'][i]}…{d['temperature_2m_max'][i]}°C, "
                     f"{WMO.get(d['weather_code'][i], '')}, осадки {d['precipitation_probability_max'][i]}%")
    return "\n".join(lines)


def screenshot_b64(max_side=1024) -> str:
    from PIL import ImageGrab

    img = ImageGrab.grab(all_screens=False)
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode()


async def look_at_screen(question: str) -> str:
    img = await asyncio.to_thread(screenshot_b64)
    if hooks["vision"] is None:
        return "Зрение недоступно"
    return await hooks["vision"](question, img)


def list_windows() -> str:
    import pygetwindow as gw

    titles = [w.title for w in gw.getAllWindows() if w.title.strip() and w.visible and w.width > 50]
    return "\n".join(dict.fromkeys(titles)) or "(нет окон)"


def _find_window(title: str):
    import pygetwindow as gw

    t = title.lower()
    for w in gw.getAllWindows():
        if t in w.title.lower() and w.title.strip():
            return w
    return None


def focus_window(title: str) -> str:
    w = _find_window(title)
    if not w:
        return f"Окно «{title}» не найдено"
    try:
        if w.isMinimized:
            w.restore()
        w.activate()
    except Exception:
        import pyautogui

        pyautogui.press("alt")
        w.activate()
    return f"Активно окно: {w.title}"


def minimize_window(title: str) -> str:
    if title.strip().lower() in ("all", "все"):
        import pyautogui

        pyautogui.hotkey("win", "d")
        return "Все окна свёрнуты"
    w = _find_window(title)
    if not w:
        return f"Окно «{title}» не найдено"
    w.minimize()
    return f"Свёрнуто: {w.title}"


def find_files(pattern: str, folder: str = "~") -> str:
    base = _path(folder or "~")
    found = []
    skip = {"AppData", "node_modules", ".git", "$Recycle.Bin", "Windows", ".venv"}
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
        for f in Path(root).glob(pattern) if any(ch in pattern for ch in "*?[") else []:
            found.append(str(f))
        if not any(ch in pattern for ch in "*?["):
            found += [str(Path(root) / f) for f in files if pattern.lower() in f.lower()]
        if len(found) >= 50:
            break
    return "\n".join(found[:50]) or "Ничего не найдено"


def list_directory(path: str) -> str:
    p = _path(path)
    items = []
    for e in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:200]:
        items.append(("[папка] " if e.is_dir() else "") + e.name)
    return _clip("\n".join(items) or "(пусто)")


def read_file(path: str) -> str:
    return _clip(_path(path).read_text("utf-8", errors="replace"))


def write_file(path: str, content: str) -> str:
    p = _path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, "utf-8")
    return f"Записано {len(content)} символов в {p}"


def system_info() -> str:
    vm = psutil.virtual_memory()
    lines = [
        f"Время: {datetime.now():%d.%m.%Y %H:%M}",
        f"ОС: {platform.platform()}",
        f"CPU: {psutil.cpu_percent(interval=0.5)}% ({psutil.cpu_count()} потоков)",
        f"Оперативная память: свободно {vm.available / 2**30:.1f} ГБ, занято {vm.used / 2**30:.1f} ГБ, "
        f"всего {vm.total / 2**30:.1f} ГБ (загрузка {vm.percent}%)",
    ]
    for d in psutil.disk_partitions():
        try:
            u = psutil.disk_usage(d.mountpoint)
            lines.append(f"Диск {d.device}: свободно {u.free / 2**30:.0f} из {u.total / 2**30:.0f} ГБ")
        except Exception:
            pass
    bat = psutil.sensors_battery()
    if bat:
        lines.append(f"Батарея: {bat.percent}%")
    up = datetime.now() - datetime.fromtimestamp(psutil.boot_time())
    lines.append(f"Время работы: {up.days} д {up.seconds // 3600} ч")
    return "\n".join(lines)


def list_processes(filter: str = "") -> str:
    procs = []
    for p in psutil.process_iter(["name", "memory_info"]):
        n = p.info["name"] or ""
        if filter and filter.lower() not in n.lower():
            continue
        mem = p.info["memory_info"].rss if p.info["memory_info"] else 0
        procs.append((mem, n))
    procs.sort(reverse=True)
    return "\n".join(f"{n}: {m / 2**20:.0f} МБ" for m, n in procs[:40])


def set_volume(percent: int) -> str:
    import pyautogui

    percent = max(0, min(100, int(percent)))
    pyautogui.press("volumedown", presses=50)
    pyautogui.press("volumeup", presses=round(percent / 2))
    return f"Громкость ~{percent}%"


def media_control(action: str) -> str:
    import pyautogui

    key = {"playpause": "playpause", "next": "nexttrack", "prev": "prevtrack",
           "mute": "volumemute"}.get(action.lower(), "playpause")
    pyautogui.press(key)
    return f"Нажато: {key}"


def type_text(text: str) -> str:
    import pyautogui
    import pyperclip

    # через буфер — так работает кириллица
    old = pyperclip.paste()
    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")
    pyperclip.copy(old)
    return "Текст напечатан"


def press_keys(keys: str) -> str:
    import pyautogui

    parts = [k.strip().lower() for k in keys.split("+") if k.strip()]
    pyautogui.hotkey(*parts)
    return f"Нажато: {keys}"


def clipboard(action: str, text: str = "") -> str:
    import pyperclip

    if action == "set":
        pyperclip.copy(text)
        return "Скопировано в буфер"
    return _clip(pyperclip.paste() or "(буфер пуст)")


# ---------- мышь и окна ----------
async def _locate(target: str):
    img = await asyncio.to_thread(screenshot_b64, 1280)
    if hooks["ground"] is None:
        raise RuntimeError("зрение недоступно")
    pt = await hooks["ground"](target, img)
    if not pt:
        return None
    import pyautogui

    w, h = pyautogui.size()
    return int(pt[0] / 1000 * w), int(pt[1] / 1000 * h)


async def click_on(target: str, action: str = "click") -> str:
    import pyautogui

    pt = await _locate(target)
    if not pt:
        return f"Не нашла на экране: {target}"
    x, y = pt
    pyautogui.moveTo(x, y, duration=0.25)
    if action == "double":
        pyautogui.doubleClick()
    elif action == "right":
        pyautogui.rightClick()
    elif action != "move":
        pyautogui.click()
    return f"{'Навела' if action == 'move' else 'Кликнула'} по «{target}» в точке ({x}, {y})"


def mouse_click_xy(x: int, y: int, button: str = "left", double: bool = False) -> str:
    import pyautogui

    pyautogui.moveTo(int(x), int(y), duration=0.2)
    pyautogui.click(button=button or "left", clicks=2 if double else 1, interval=0.08)
    return f"Клик {button} в ({x}, {y})"


async def drag(from_target: str, to_target: str) -> str:
    import pyautogui

    a, b = await _locate(from_target), await _locate(to_target)
    if not a or not b:
        return "Не нашла, откуда или куда тащить"
    pyautogui.moveTo(*a, duration=0.2)
    pyautogui.dragTo(*b, duration=0.6, button="left")
    return f"Перетащила из {a} в {b}"


def scroll(direction: str, amount: int = 5) -> str:
    import pyautogui

    pyautogui.scroll((1 if direction == "up" else -1) * 120 * int(amount or 5))
    return f"Прокрутила {direction}"


def _active_window():
    from pywinauto import Desktop

    return Desktop(backend="uia").window(active_only=True)


def ui_elements() -> str:
    win = _active_window()
    out = [f"Окно: {win.window_text()}"]
    want = {"Button", "Edit", "MenuItem", "Hyperlink", "TabItem", "ListItem", "CheckBox", "ComboBox", "TreeItem",
            "RadioButton", "SplitButton"}
    n = 0
    for el in win.descendants():
        try:
            ct = el.element_info.control_type
            name = el.window_text().strip()
            if ct in want and name and el.is_visible():
                r = el.rectangle()
                out.append(f"[{ct}] {name[:60]}  @({(r.left + r.right) // 2}, {(r.top + r.bottom) // 2})")
                n += 1
        except Exception:
            continue
        if n >= 80:
            break
    return "\n".join(out)


def click_element(name: str) -> str:
    win = _active_window()
    low = name.lower()
    for el in win.descendants():
        try:
            if low in el.window_text().lower() and el.is_visible():
                el.click_input()
                return f"Нажала «{el.window_text()}»"
        except Exception:
            continue
    return f"Элемент «{name}» не найден"


def wait(seconds: float) -> str:
    import time

    time.sleep(max(0.2, min(30, float(seconds))))
    return f"Подождала {seconds} с"


OUTFITS = ['Обычная', 'Неко', 'Ведьмочка', 'Ангел', 'Демонесса', 'Киберпанк', 'Школьница', 'Зимняя']


def set_outfit(name: str) -> str:
    match = next((o for o in OUTFITS if o.lower() == name.strip().lower()), None) \
        or next((o for o in OUTFITS if name.strip().lower()[:4] in o.lower()), None)
    if not match:
        return "Нет такого наряда. Есть: " + ", ".join(OUTFITS)
    settings.update({"outfit": match})
    if hooks.get("settings_changed"):
        hooks["settings_changed"]({"outfit": match})
    return f"Переоделась: {match}"


def set_reminder(minutes: float, text: str) -> str:
    if hooks["remind"] is None:
        return "Напоминания недоступны"
    hooks["remind"](text, float(minutes) * 60)
    when = datetime.now() + timedelta(minutes=float(minutes))
    return f"Напоминание поставлено на {when:%H:%M}: {text}"


async def sing_song(query: str) -> str:
    if hooks["sing"] is None:
        return "Пение недоступно"
    return await hooks["sing"](query)


FUNCS = {f["function"]["name"]: globals()[f["function"]["name"]] for f in TOOLS}


def describe(name: str, args: dict) -> str:
    if name == "run_command":
        return args.get("command", "")
    if name == "write_file":
        return f"Записать файл {args.get('path')}"
    return f"{name}: {args}"


async def execute(name: str, args: dict, confirm) -> str:
    mode = settings.get().get("pc_access", "confirm")
    if mode == "off":
        return "Доступ к компьютеру отключён в настройках."
    fn = FUNCS.get(name)
    if not fn:
        return f"Неизвестный инструмент {name}"
    risky = mode == "confirm" and name in NEEDS_CONFIRM
    if (mode == "full" and settings.get().get("confirm_destructive", True) and name == "run_command"
            and DESTRUCTIVE_RE.search(args.get("command", ""))):
        risky = True
    if risky and not await confirm(name, args):
        return "Пользователь отклонил это действие."
    try:
        if inspect.iscoroutinefunction(fn):
            return await fn(**args)
        return await asyncio.to_thread(fn, **args)
    except TypeError as e:
        return f"Неверные аргументы: {e}"
    except Exception as e:
        return f"Ошибка: {e}"
