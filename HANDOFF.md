# Промт для новой сессии — проект «Юки» (ИИ-ассистент с 3D-аватаром)

Скопируй всё ниже в новую сессию Claude Code.

---

Ты продолжаешь работу над моим проектом **«Юки»** — голосовой ИИ-ассистенткой (аниме-девушка, 3D VRM-аватар
на рабочем столе Windows), которая слышит меня, отвечает голосом по-русски, управляет ПК, помнит меня и поёт.
Пользователь общается по-русски, отвечай по-русски, коротко и по делу.

## Где что лежит

- **Папка проекта:** `D:\Projects\Yuki` (перенесена 2026-10-12 из временной scratch-папки; на C: мало места).
  venv-ы переносимы (`pyvenv.cfg` указывает на базовый Python 3.11), пути в коде относительные.
- **GitHub (приватный):** https://github.com/AshcookieKing/Yuki-AI-Assistant — ветка `main`, `gh` авторизован как AshcookieKing.
  Отдельный git-репозиторий внутри папки проекта (домашняя папка `C:\Users\mashi` — тоже git-репо, не путать).
- **Ollama:** `%LOCALAPPDATA%\Programs\Ollama\ollama.exe` (не в PATH), версия 0.40. Модели: `qwen3.5:9b` (основная локальная),
  `embeddinggemma:300m` (память), `qwen3:8b` (старая).
- **ПК:** RTX 3060 12 ГБ, 32 ГБ ОЗУ, Windows 11. Python 3.11 через `py -3.11` (`C:\Python312` сломан — не использовать).

## Архитектура

| Файл | Что делает |
|---|---|
| `server.py` | FastAPI на 127.0.0.1:8765: API, WebSocket-хаб (общий диалог для всех окон), фильтр «это мне?», пение, прогрев голоса, раздача `static/` |
| `assistant/brain.py` | Два прохода: **агент** (строгий промпт, инструменты, до 15 шагов) → **персонаж** (характер, стрим, озвучка по фразам). `ACTION_RE` решает, нужен ли агент. `is_addressed()` — фильтр фоновой речи. Честность: если действий не было — запрет «делать вид» |
| `assistant/llm.py` | Клиент: Ollama (`/api/chat`) и OpenAI-совместимые облака (Groq/Gemini/OpenRouter) |
| `assistant/tools.py` | 34 инструмента: PowerShell, программы, окна, мышь (`click_on` через зрение-grounding, `ui_elements`/`click_element` через pywinauto), клавиатура, YouTube, погода (open-meteo), веб (Tavily по ключу), зрение экрана, файлы, напоминания, пение, наряды. `DESTRUCTIVE_RE` — в полном доступе всё равно спрашивает перед удалением/форматированием/выключением |
| `assistant/memory.py` | Долговременная память (`data/memory.json`, эмбеддинги embeddinggemma на CPU), дневник личности `data/self.md`, история `data/history.json` |
| `assistant/activity.py` | Фоновое изучение активных окон (`data/activity.json`, банки/пароли/инкогнито не пишутся), выводы в память раз в 30 мин |
| `assistant/tts.py` | Голос: `edge` (Edge TTS, сейчас голос en-US-AvaMultilingualNeural +30 Гц, без фильтра — MP3 как есть), `silero` (v5), `chatterbox` (нейро-голос, см. ниже), + перенос голоса Seed-VC (`voice_clone`) |
| `assistant/voice.py` | Управление процессами: певческий/перенос голоса `singer/worker.py` (.venv-sing, порт 8766, одна копия под замком, модели пения на CPU пока не поют) и нейро-голос `singer/tts_worker.py` (.venv-tts, порт 8767) |
| `assistant/stt.py` | Whisper large-v3-turbo, `int8_float16` на CUDA, подсказка словаря (Стим/Discord...) |
| `singer/worker.py` | Seed-VC: `/convert` (речь), `/sing` (yt-dlp → Demucs htdemucs → Seed-VC 44k, 30 шагов → сведение со сценическим эхом `vocal_chain`) |
| `singer/tts_worker.py` | **Новое:** Chatterbox Multilingual (resemble-ai/chatterbox, MIT), русский, клонирует тембр из `data/voice_ref_tts.wav` |
| `static/js/` | `app.js` (режимы mascot/panel/full, аудио-очередь, перебивание с приглушением `unduck`), `avatar.js`, `face.js` (57 морфов VRoid, 23 эмоции, румянец, значки), `gestures.js` (жесты + движения телом: dance, bow, nod…, idle-действия), `outfits.js` (8 нарядов: перекраска + 3D-аксессуары), `lipsync.js`, `mic.js` (VAD с ducking) |
| `desktop/` | Electron-фигурка: прозрачное окно, клики насквозь, трей. **Новое:** packaged-режим (`app.isPackaged` → рабочая папка `%LOCALAPPDATA%\Yuki\backend`), окно первого запуска `setup.html` + `setup-preload.js` |
| `setup.bat`, `setup_voice.bat`, `start.bat` | Установка из исходников / голосовые модули (Chatterbox + Seed-VC) / запуск |

Логи: `data/server.log`, `data/actions.log` (все действия + строки TIMING), `data/voice_worker*.log`, `data/tts_worker*.log`.

## Что сделано в последней сессии (закоммичено в 0fdfc28, но не проверено вживую)

1. **Облачные модели Ollama** (`server.py`: `cloud_models()`, `/api/ollama/cloud_check`; `app.js`: `refreshModels`/`checkCloud`
   с группами 💻/☁ в списке моделей; настройка `cloud_screen`). Теги: `имя:cloud` или `имя:тег-cloud`.
   ⚠️ Облако отвечает **401** — пользователь ещё **не вошёл в аккаунт Ollama** (Sign in в приложении Ollama делает он сам,
   входить за него нельзя). Бесплатный план: стартовые кредиты, дальше оплата по токенам — предупредить.
2. **Нейро-голос Chatterbox**: установлен в `.venv-tts` (torch 2.6.0+cu124 — ставить с `--force-reinstall --no-deps`, иначе
   остаётся CPU-версия). Образец `data/voice_ref_tts.wav` (= `voice_samples/loli/out_ref_loli_plain.wav`). Результат:
   ~404 Гц, Whisper распознаёт слово в слово, но скорость ~1.4–1.9 с на 1 с звука (fp16/autocast не ускоряют).
   Настройки: `tts_engine="chatterbox"`, `tts_exaggeration` 0.75, `tts_cfg` 0.35. `data/voice_ref.wav` заменён на этот же
   чистый образец (старый — `data/voice_ref_old_loli.wav`).
3. **Установщик .exe**: `desktop/package.json` (electron-builder 25, NSIS, per-user, ярлыки «Юки»), `desktop/icon.ico` 256px,
   `setup.bat` понимает `YUKI_PACKAGED=1` и пишет `data/.installed`, `setup_voice.bat` ставит Chatterbox и качает Seed-VC zip.
   Первая сборка упала только из-за иконки (<256px) — иконку уже переделал, **сборку надо перезапустить**:
   `cd desktop && npx electron-builder --win nsis --publish never` → `dist/Yuki-Setup-1.1.0.exe`.

## На чём остановились (следующие шаги)

1. Перенести проект в постоянную папку (см. выше), поправить ярлыки/пути.
2. Перезапустить приложение, включить нейро-голос:
   `POST /api/settings {"tts_engine":"chatterbox","tts_exaggeration":0.75,"tts_cfg":0.35,"voice_clone":false}` и замерить
   задержку первой фразы в живом разговоре (`data/actions.log`, строки TIMING). Если слишком медленно — предложить
   пользователю Edge/Seed-VC как быстрый режим.
3. Когда пользователь войдёт в Ollama — выбрать облачную модель (кандидаты с tools+vision: `kimi-k3`, `deepseek-v4.1-flash`,
   `gemma4:31b`), проверить `/api/ollama/cloud_check`, агент 8/8 тестом (скрипт сравнения был в scratchpad — переписать).
   Тогда видеокарта освобождается под Whisper + Chatterbox.
4. Собрать установщик, проверить его на чистом запуске (первый запуск → окно «Подготовка» → setup.bat в видимой консоли).
5. Обновить README (облако, нейро-голос, установщик), закоммитить всё и запушить; опубликовать установщик:
   `gh release create v1.1.0 dist/Yuki-Setup-1.1.0.exe --title "Юки 1.1" --notes "..."` (репо приватный — согласовать с пользователем).

## Выпуск новой версии (автообновление)

Установленная Юки (electron-updater, `desktop/main.js` → `initUpdater`) при запуске и раз в 4 часа читает
`latest.yml` последнего релиза на GitHub (репо публичный), качает новую версию в фоне и предлагает перезапуститься
(или ставит при выходе). В трее — «Проверить обновления». Чтобы выпустить версию:
1. поднять `version` в `desktop/package.json` (например 1.2.0);
2. `cd desktop && npx electron-builder --win nsis --publish never`;
3. `gh release create v1.2.0 release\Yuki-Setup-1.2.0.exe release\Yuki-Setup-1.2.0.exe.blockmap release\latest.yml --title "Юки 1.2" --notes "..."`
   — **`latest.yml` обязателен**, без него обновление не увидят.
Код бэкенда обновляется вместе с установщиком (`syncBackend` копирует его в `%LOCALAPPDATA%\Yuki\backend` при запуске);
если менялся `requirements.txt` — пользователю нужно перезапустить установку библиотек (пока не автоматизировано).

## Важные правила и грабли

- **Запускать Ollama/Electron/воркеры только с `dangerouslyDisableSandbox: true`** — из песочницы Ollama ловит
  «untrusted mount point», а процессы наследуют ограничения.
- **Убивать процессы деревом** (python.exe из venv — лаунчер с дочерним процессом). Удобно:
  `Get-CimInstance Win32_Process | ? { $_.CommandLine -like '*server.py*' -or $_.CommandLine -like '*worker.py*' } | % { Stop-Process -Id $_.ProcessId -Force }`.
- Перезапуск приложения: убить server/worker/electron, затем
  `Start-Process "<проект>\desktop\node_modules\electron\dist\electron.exe" -ArgumentList "desktop" -WorkingDirectory "<проект>"`.
- **Видеопамять 12 ГБ — главный ограничитель.** Несколько копий Seed-VC или локальная модель + Seed-VC + Whisper = тормоза
  в 10–30 раз и «она не отвечает». Не запускай тесты пения, пока пользователь разговаривает с ней.
- Пользователь часто разговаривает с ней прямо во время работы — тестовые реплики через WebSocket перемешиваются с его.
- В bash-heredoc с Python не писать `\n`/`\b` в строках (превращаются в настоящие символы) — патчи писать файлом через Write.
- **Нельзя:** создавать/входить в аккаунты за пользователя, вводить пароли/ключи, тихо скачивать и устанавливать чужие
  программы (прошлая попытка автоустановщика Python/Ollama была заблокирована — не повторять; установщик только открывает
  официальные сайты, а установку делает пользователь). Не клонировать голоса реальных людей/персонажей (например Нейроны).
- Управление мышью пользователь разрешил явно. Режим доступа сейчас «полный» (`pc_access=full`), необратимые команды — с подтверждением.
- Пользователь не любит долгие ответы и болтовню: реплики 1–2 предложения, `smart_mode` по умолчанию выключен.
- В коммитах, релизах и файлах репозитория — никаких упоминаний Claude (ни Co-Authored-By, ни «Generated with»).

## Сессия 27.11.2026 (версия 1.2.0, собрана, НЕ опубликована)

- Мозг `yuki-qwen3.6` = Qwen3.6 35B-A3B UD-IQ3_XXS (Unsloth GGUF) + mmproj, создан `ollama create` из `models/Modelfile.yuki`
  (RENDERER/PARSER qwen3.5). Замер: прогретая — первое слово 0.49 с, 25 ток/с, 28%/72% CPU/GPU, tool calls работают.
  Холодная загрузка ~2.5 мин — поэтому `_warm_llm()` в server.py при старте.
- Ускорение: системный промпт неизменный (время/память/заметки/активность — `with_context()` перед последней репликой),
  первая фраза режется по запятой (`pop_sentences(first_phrase=True)`).
- Голос `tts_engine="espeech"`: ESpeech-TTS-1 RL-V2 (F5-TTS) в `.venv-tts`, RTF 0.34–0.53 на GPU, ~390–450 Гц, Whisper
  слово в слово. Нужны `datasets<4`, `pyarrow<17` при numpy 1.26. HF_HOME = `data/hf` (на C: мало места).
  Образец по умолчанию `singer/voice/default_ref.wav/.txt`.
- База знаний Obsidian: `assistant/knowledge.py`, хранилище `obsidian_vault` (пусто → `data/vault`), индекс `data/knowledge.npz`.
- Установщик сам выбирает мозг (VRAM≥8 и ОЗУ≥24 → Qwen3.6 сжатая с HF, иначе qwen3.5:9b), ставит ESpeech.
- ⚠️ На C: осталось ~4 ГБ (модели Ollama там ~41 ГБ). Кандидаты на удаление: qwen3:8b, saiga, dolphin, gemma3 — спросить.
- Живой проверки в приложении не было (пользователь играл в Arma).

## Сессия 28.11.2026 — облачный голос Qwen3.8-Omni (не опубликовано, не проверено с настоящим ключом)

- `assistant/omni.py`: клиент `qwen3.8-omni-flash-realtime` (WebSocket, ручной режим turn_detection=null), голос Cici,
  инструменты ПК через function calling, звук отдаётся окну кусками WAV 24 кГц (0.6 с, потом по 2 с) с подписями.
  Адрес: omni_url → иначе `wss://{omni_workspace}.ap-southeast-1.maas.aliyuncs.com/api-ws/v1/realtime` → иначе
  `wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime` (последний по документации не подтверждён — проверить).
- server.py: `/api/stt` запоминает WAV фразы, `Hub.omni_turn` → при любой ошибке `omni.mark_down` (60 с) и локальный ответ.
  `/api/omni/check`. Настройки omni_* во вкладке «Голос».
- Проверено на локальной заглушке протокола (scratchpad omni_test.py): событие, ключ, инструмент, весь звук; откат на локальный.
- Неизвестно: принимает ли облако текстовый ввод `conversation.item.create` message/input_text (для набранных реплик).
- Мягкий локальный голос: начаты варианты в `voice_samples/soft` (CPU, 3 из 5 готовы), фильтр `soften()` в scratchpad —
  не встроено.

## 28.11 — голос Ono_Anna (Qwen3-TTS 1.7B CustomVoice) по умолчанию
- `singer/qtts_worker.py` в `.venv-qtts` (порт 8768): faster-qwen3-tts (CUDA-графы) + transformers==5.15.1
  (5.24 ломает qwen-tts-hf: «property 'upsample' … has no setter»). Эмоция фразы → инструкция манеры (`voice.QTTS_EMO`).
- Замер без Arma, всё загружено (Qwen3.6 28/72 CPU/GPU + Whisper + голос = 11.8/12 ГБ VRAM): мозг 0.4 с до первого
  слова, 47 ток/с; голос 1.5–3 с на фразу (RTF ~0.5–0.6), первая после загрузки — дольше.
- С запущенной Arma (arma3profiling ~22 ГБ commit) мозг не грузится: не хватает памяти Windows.
