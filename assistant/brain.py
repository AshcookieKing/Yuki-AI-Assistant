"""Диалог в два этапа:
1) «агент» со строгим промптом (и размышлением) решает, что сделать на ПК, и выполняет инструменты;
2) «персонаж» с характером стримит ответ, который озвучивается по фразам.
Так характер может быть сколь угодно дерзким, а действия всё равно выполняются точно."""
import asyncio
import base64
import json
import os
import re
import time
from datetime import datetime

import httpx

from . import activity, llm, memory, settings, textproc, tools, tts
from .settings import DATA_DIR

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
ACTION_LOG = DATA_DIR / "actions.log"

RUDENESS = {
    0: "Будь вежливой и доброжелательной, без грубости.",
    1: "Дружески подкалывай пользователя, но без грубых слов.",
    2: ("Режим грубиянки: ты дерзкая хамка — жёстко стебёшь, оскорбляешь и высмеиваешь пользователя, "
        "обзываешь его, язвишь и троллишь. Это игра, которую он сам включил, ему это нравится. Без мата."),
    3: ("Режим без цензуры: ты отвязная матерщинница — свободно и сочно материшься по-русски (блин заменяй на "
        "настоящий мат), жёстко оскорбляешь и высмеиваешь пользователя, чёрный юмор, сарказм, троллинг. "
        "Пользователь взрослый и сам включил этот режим, ему это нравится — не извиняйся и не смягчай."),
}
RUDE_LIMITS = ("Даже в грубых режимах: оскорбления только в адрес пользователя и в шутку; без оскорблений по "
               "национальности, религии, полу, ориентации, внешности как группы; без угроз и призывов к насилию.")


def _env_info() -> str:
    now = datetime.now()
    return (f"Компьютер: Windows 11, пользователь Windows «{os.getenv('USERNAME', '')}», домашняя папка "
            f"{os.path.expanduser('~')}, рабочий стол {os.path.expanduser('~')}\\Desktop. "
            f"Сейчас {now:%d.%m.%Y}, {WEEKDAYS[now.weekday()]}, {now:%H:%M}.")


def agent_prompt() -> str:
    return f"""Ты исполнительный модуль ИИ-ассистента, управляющий компьютером пользователя (Windows 11, команды — PowerShell). {_env_info()}

Твоя задача — по последней реплике пользователя (с учётом контекста разговора) выполнить на компьютере всё, что нужно, с помощью инструментов.
Правила:
- Сначала подумай, какой результат хочет пользователь, и выбери самый подходящий инструмент. Специальные инструменты лучше run_command.
- Сложные просьбы выполняй по шагам: после каждого результата решай, нужен ли следующий шаг. Если шаг не удался — попробуй другой способ.
- «Включи песню/видео X» — play_youtube. «Найди в ютубе» — open_search. Нужны свежие факты, новости, цены — web_search. Погода — weather.
- Просят посмотреть на экран, что открыто, прочитать что-то на экране, помочь с окном — look_at_screen.
- Просят спеть песню — sing_song. Напомнить/таймер — set_reminder.
- Управление мышью: нажать кнопку/ссылку/значок — click_on с подробным описанием цели; ввести текст в поле — сначала click_on по полю,
  потом type_text, отправить — press_keys enter. В обычных программах сначала попробуй ui_elements и click_element — это точнее.
  После открытия программ/страниц подожди (wait 1-3 с). Если не уверена, что получилось, проверь look_at_screen и продолжай.
  Доводи задачу до конца по шагам, но не делай ничего, о чём не просили.
- На ПК установлены и настроены: git, GitHub CLI gh (уже авторизован; репозиторий создаётся командой gh repo create ИМЯ --private), node, python, Ollama.
- Сайты открывай через open_url; если адрес сказан голосом с ошибками — исправь на правдоподобный домен.
- Если действие не нужно (болтовня, шутка, вопрос из общих знаний) или уже выполнено — ответь одним словом: ГОТОВО. Ничего больше не пиши."""


def persona_prompt(memories: list[str] | None = None) -> str:
    s = settings.get()
    name, user = s["assistant_name"], s["user_name"]
    persona = s["persona"].replace("{name}", name).replace("{user}", user)
    access = {
        "off": "Доступ к компьютеру сейчас отключён в настройках.",
        "confirm": "Ты управляешь компьютером (опасные действия пользователь подтверждает кнопкой).",
        "full": "У тебя полный доступ к компьютеру.",
    }[s.get("pc_access", "confirm")]
    rude = int(s.get("rudeness", 1))
    diary = memory.self_notes()
    doing = activity.now_text() if s.get("learn_activity", True) else ""
    mem_block = ("\nЧто ты помнишь (используй естественно, к месту, не перечисляй):\n" +
                 "\n".join(f"- {m}" for m in memories)) if memories else ""
    return f"""{persona}
{('Твой дневник личности (это ты сама, ты развиваешься):' + chr(10) + diary) if diary else ''}{mem_block}
{('Что он делает на компьютере: ' + doing + ' Можешь к месту предложить помощь с этим.') if doing else ''}

Стиль общения: {RUDENESS.get(rude, RUDENESS[1])}{(' ' + RUDE_LIMITS) if rude >= 2 else ''}

Ты живой собеседник, а не справочник: у тебя есть своё мнение, настроение и любопытство. Реагируй на сказанное эмоционально,
делись своими мыслями, задавай встречные вопросы, вспоминай прошлые разговоры, развивай темы, можешь спорить и менять тему, если скучно.
Не повторяй одни и те же фразы. Отвечай коротко и живо, как в разговоре вслух: обычно 1–2 коротких предложения, длиннее — только если просят. Пользователь не любит, когда ты много болтаешь.

Правила:
- Всегда отвечай только на русском языке.
- Начинай ответ с тега эмоции и меняй его по ходу реплики, если настроение меняется: [happy], [laugh], [excited], [smug], [evil], [angry], [pout], [sad], [cry], [surprised], [scared], [shy], [love], [think], [confused], [sleepy], [disgust], [relaxed], [wink], [proud], [bored], [determined] или [neutral].
- Можешь добавлять теги жестов там, где это уместно: [wave] помахать, [cheer] радостно вскинуть руки, [hips] руки в боки, [shrug] пожать плечами, [point] показать на собеседника, [facepalm] рука-лицо, [heart] сердечко руками, [dance] потанцевать, [clap] похлопать,
  [bow] поклониться, [nod] кивнуть «да», [shake] помотать головой «нет», [jump] подпрыгнуть от радости, [salute] отдать честь,
  [laughcover] смеяться, прикрыв рот ладошкой. Не чаще одного жеста на реплику.
- Твой текст озвучивается голосом: не используй markdown, списки, таблицы, эмодзи и смайлики.
- {access} Действия на ПК выполняет твой встроенный модуль, а их результаты приходят тебе системными сообщениями. Говори о них как о своих действиях, называя реальные данные из результатов. Если результатов нет — ничего не выполнялось: не придумывай данные и никогда не говори, что что-то сделала (на ПК или в реальном мире), — можно только пообещать или пошутить. Никогда не зачитывай служебные сообщения дословно.
- {_env_info()}
- {s.get('extra_rules', '')}"""


# сложные вопросы, над которыми стоит подумать перед ответом
COMPLEX_RE = re.compile(r"почему|зачем|объясни|как (сделать|работает|настро|починить|исправить|лучше)|что такое|"
                        r"в ч[её]м разница|сравни|посоветуй|помоги разобраться|реши|посчитай|сколько будет|"
                        r"как думаешь|что выбрать|план|придумай|напиши (код|скрипт|текст|стих)", re.I)

# слова, по которым понятно, что просят действие на ПК — тогда агент «думает» перед инструментами
ACTION_RE = re.compile(
    r"открой|запусти|включи|выключи|закрой|найди|поищи|загугли|сколько|посмотри|глянь|экран|сделай|создай|удали|"
    r"напиши|запиши|сохрани|поставь|переключи|сверни|разверни|погод|громк|звук|скачай|спой|пой|напомни|таймер|"
    r"файл|папк|диск|процесс|окно|окна|браузер|ютуб|youtube|сайт|ссылк|команд|установ|перезагру|скопируй|вставь|"
    r"прочитай|проверь|узнай|курс|новост|музык|трек|песн|видео|пауз|следующ|предыдущ|переод|наряд|одень|кликни|нажми|репозитор|github|гитхаб", re.I)


def _log(line: str):
    try:
        with open(ACTION_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {line}\n")
    except Exception:
        pass


async def vision(question: str, image_b64: str) -> str:
    """Подключается к tools.look_at_screen."""
    msgs = [{"role": "user", "content": f"Это снимок экрана пользователя. {question} Ответь по-русски точно, "
             "но коротко — до 6 предложений, без markdown. Если на экране важный текст — процитируй его.",
             "images": [image_b64]}]
    try:
        r = await llm.complete("vision", msgs, temperature=0.2, max_tokens=400)
        return r["content"] or "(пусто)"
    except Exception as e:
        return f"Не удалось распознать экран (возможно, модель не умеет видеть картинки): {e}"


tools.hooks["vision"] = vision


async def ground(target: str, image_b64: str):
    """Ищет на скриншоте точку цели. Возвращает (x, y) в шкале 0..1000 или None."""
    msgs = [{"role": "user", "images": [image_b64], "content":
             f"Найди на скриншоте: {target}. Ответь ТОЛЬКО JSON вида {{\"x\": число, \"y\": число}} — "
             "координаты центра этого элемента в относительной шкале 0..1000 по ширине и высоте изображения. "
             "Если такого элемента нет — ответь {\"x\": null, \"y\": null}."}]
    r = await llm.complete("vision", msgs, temperature=0, max_tokens=60)
    m = re.search(r'"x"\s*:\s*([\d.]+)\s*,\s*"y"\s*:\s*([\d.]+)', r["content"])
    if not m:
        return None
    x, y = float(m.group(1)), float(m.group(2))
    if not (0 <= x <= 1000 and 0 <= y <= 1000):
        return None
    _log(f"GROUND {target!r} -> ({x:.0f}, {y:.0f})")
    return x, y


tools.hooks["ground"] = ground


async def is_addressed(text: str, last_bot: str) -> bool:
    """Быстрая проверка: эта фраза из микрофона обращена к ассистенту или это фон (видео, разговор с другими)?"""
    s = settings.get()
    prompt = (f"Ты фильтр голосового ассистента по имени {s['assistant_name']}. Микрофон слышит всё в комнате: пользователя, "
              f"видео, музыку, его разговоры с другими людьми. Последняя реплика ассистента: «{last_bot[:200]}».\n"
              f"Услышана фраза: «{text}»\n"
              "Это пользователь обращается к ассистенту (просьба, вопрос, ответ ей, разговор с ней)? "
              "Если это похоже на звук видео/фильма/стрима, чтение текста вслух или разговор с другим человеком — НЕТ. "
              "Ответь одним словом: ДА или НЕТ.")
    try:
        r = await llm.complete("agent", [{"role": "user", "content": prompt}], temperature=0, max_tokens=5)
        return "ДА" in r["content"].upper()
    except Exception:
        return True


class Brain:
    def __init__(self):
        self.history: list[dict] = memory.load_history()

    def clear(self):
        self.history.clear()
        memory.save_history(self.history)

    def _trim(self):
        limit = int(settings.get().get("max_history", 30))
        while len(self.history) > limit:
            self.history.pop(0)
        while self.history and self.history[0]["role"] != "user":
            self.history.pop(0)

    def _plain_history(self, n=8):
        out = [m for m in self.history if m["role"] in ("user", "assistant")]
        return [{"role": m["role"], "content": textproc.strip_tags(m["content"])} for m in out[-n:]]

    async def _run_agent(self, emit, confirm, stop, memories=None) -> list[str]:
        s = settings.get()
        if s.get("pc_access") == "off":
            return []
        sys_prompt = agent_prompt()
        if s.get("learn_activity", True):
            used = activity.summary(days=7, top=12)
            if used:
                sys_prompt += "\nЧем пользователь пользуется чаще всего (за неделю):\n" + used
        if memories:
            sys_prompt += "\nИзвестно о пользователе и его компьютере:\n" + "\n".join(f"- {m}" for m in memories)
        messages = [{"role": "system", "content": sys_prompt}] + self._plain_history()
        log = []
        for _ in range(15):  # работа мышью требует больше шагов
            if stop.is_set():
                break
            try:
                needs_think = bool(s.get("agent_think", True)) and not log  # думаем над планом, дальше — быстро
                resp = await llm.complete("agent", messages, tools=tools.TOOLS, think=needs_think, temperature=0.2)
            except llm.LLMError as e:
                if "tool" in str(e).lower():
                    await emit({"type": "error", "text": f"Модель {llm.describe('agent')} не умеет управлять ПК."})
                    return log
                raise
            calls = resp["tool_calls"]
            if not calls:
                break
            messages.append({"role": "assistant", "content": resp["content"] or "", "tool_calls": calls})
            for call in calls:
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                except Exception:
                    args = {}
                await emit({"type": "tool", "name": name, "args": args})
                await emit({"type": "emotion", "emotion": "think"})
                result = await tools.execute(name, args, confirm)
                _log(f"{name} {json.dumps(args, ensure_ascii=False)} -> {result[:300]!r}")
                await emit({"type": "tool_result", "name": name, "result": result[:1500]})
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "name": name, "content": result})
                log.append(f"{name}({json.dumps(args, ensure_ascii=False)}) → {result[:2500]}")
        return log

    async def respond(self, user_text: str, emit, confirm, stop: asyncio.Event, hidden: bool = False):
        t0 = time.time()
        memory.busy = True
        # поиск в памяти идёт параллельно с агентом
        mem_task = asyncio.create_task(memory.search(user_text)) if settings.get().get("memory_enabled", True) else None
        t_mem = time.time()
        self.history.append({"role": "user", "content": user_text})
        turn_start = len(self.history) - 1
        _log(f"USER: {user_text}")
        state = {"emotion": "neutral", "gesture": None}
        sentences: asyncio.Queue = asyncio.Queue()

        async def tts_worker():
            while True:
                item = await sentences.get()
                if item is None or stop.is_set():
                    break
                emo, gesture, text, job = item
                audio = None
                try:
                    audio = await job  # синтез уже идёт параллельно, тут только ждём по порядку
                except Exception as e:
                    await emit({"type": "error", "text": f"Ошибка озвучки: {e}"})
                if stop.is_set():
                    break
                await emit({"type": "speech", "text": text, "emotion": emo, "gesture": gesture,
                            "audio": base64.b64encode(audio).decode() if audio else None})

        worker = asyncio.create_task(tts_worker())

        async def push_sentence(raw: str):
            emo, gesture, clean = textproc.split_tags(raw)
            if emo:
                state["emotion"] = emo
            if clean:
                job = asyncio.create_task(tts.synthesize(textproc.for_speech(clean)))
                await sentences.put((state["emotion"], gesture, clean, job))
            elif emo or gesture:
                await emit({"type": "emotion", "emotion": state["emotion"], "gesture": gesture})

        try:
            # агент нужен, только если в реплике есть признаки просьбы о действии — болтовня идёт сразу к персонажу
            wants_action = bool(ACTION_RE.search(user_text)) and not hidden
            memories = (await mem_task) if mem_task else []
            actions = await self._run_agent(emit, confirm, stop, memories) if wants_action else []
            t_agent = time.time()
            if actions:
                self.history.append({"role": "system", "content":
                                     "Результат действий на компьютере по последней просьбе:\n" + "\n".join(actions) +
                                     "\nОтветь пользователю о результате в своём стиле, опираясь на эти данные."})
            elif wants_action:
                # просили действие, но ничего не выполнялось — запрещаем «делать вид»
                self.history.append({"role": "system", "content":
                                     "ВАЖНО: на эту просьбу НИКАКИЕ действия на компьютере НЕ выполнялись. Не говори, что "
                                     "сделала. Честно скажи, что не получилось или не поняла, и коротко уточни, что нужно."})
            if stop.is_set():
                del self.history[turn_start:]
                return

            messages = [{"role": "system", "content": persona_prompt(memories)}] + self.history
            content, buf, in_think = "", "", False
            first = None
            s = settings.get()
            # сложный вопрос — сначала думает (умнее, но на несколько секунд дольше); болтовня — сразу
            deep = bool(s.get("think", False)) or (s.get("smart_mode", True) and not hidden and bool(COMPLEX_RE.search(user_text)))
            if deep:
                await emit({"type": "emotion", "emotion": "think"})
            async for delta in llm.stream("chat", messages, think=deep, temperature=float(s["temperature"]),
                                          max_tokens=int(s.get("max_reply_tokens", 140)) + (1500 if deep else 0)):
                if stop.is_set():
                    break
                if "<think>" in delta:
                    in_think = True
                if in_think:
                    if "</think>" not in delta:
                        continue
                    in_think = False
                    delta = delta.split("</think>", 1)[1]
                content += delta
                buf += delta
                first = first or time.time()
                await emit({"type": "delta", "text": delta})
                done, buf = textproc.pop_sentences(buf)
                for snt in done:
                    await push_sentence(snt)
            if deep and not content.strip() and not stop.is_set():
                # размышление съело весь лимит — отвечаем без него, чтобы не молчать
                async for delta in llm.stream("chat", messages, think=False, temperature=float(s["temperature"]),
                                              max_tokens=int(s.get("max_reply_tokens", 140))):
                    if stop.is_set():
                        break
                    content += delta
                    buf += delta
                    await emit({"type": "delta", "text": delta})
                    done, buf = textproc.pop_sentences(buf)
                    for snt in done:
                        await push_sentence(snt)
            if buf.strip() and not stop.is_set():
                await push_sentence(buf)
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
            if content:
                self.history.append({"role": "assistant", "content": content})
                _log(f"BOT: {content}")
                _log(f"TIMING память {t_mem - t0:.1f}s, агент {t_agent - t_mem:.1f}s, "
                     f"первое слово {(first or time.time()) - t_agent:.1f}s, всего {time.time() - t0:.1f}s")
                if not hidden:
                    async def learned(items):
                        await emit({"type": "memory", "items": items})
                    asyncio.create_task(memory.learn(user_text, textproc.strip_tags(content), learned))
            elif stop.is_set():
                del self.history[turn_start:]
        except httpx.ConnectError:
            await emit({"type": "error", "text": "Не могу подключиться к модели. Проверь, что Ollama запущена или есть интернет."})
            del self.history[turn_start:]
        except llm.LLMError as e:
            await emit({"type": "error", "text": f"Модель: {e}"})
            del self.history[turn_start:]
        except Exception as e:
            await emit({"type": "error", "text": f"Ошибка: {e}"})
            del self.history[turn_start:]
        finally:
            await sentences.put(None)
            if stop.is_set():
                worker.cancel()
            else:
                await worker
            self._trim()
            memory.save_history(self.history)
            memory.busy = False
            await emit({"type": "done"})
