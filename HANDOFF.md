# Промт для новой сессии — проект «Юки» (ИИ-ассистент с 3D-аватаром)

Скопируй всё ниже в новую сессию Claude Code.

---

Ты продолжаешь работу над моим проектом **«Юки»** — голосовой ИИ-ассистенткой (аниме-девушка, 3D VRM-аватар
на рабочем столе Windows), которая слышит меня, отвечает голосом по-русски, управляет ПК, помнит меня и поёт.
Пользователь общается по-русски, отвечай по-русски, коротко и по делу.

## Где что лежит

- **Папка проекта (рабочая копия):**
  `C:\Users\mashi\AppData\Roaming\Claude\scratch-workspaces\0eb1e7c8-ddef-491e-aa94-ef83300cb019\28c3c25a-b9bc-40cd-a663-9d4eb833d330\scratch-2026-10-07-d7c4bf`
  ⚠️ Это временная папка прошлой сессии — **первым делом перенеси проект в постоянное место**
  (например `C:\Yuki` или `D:\Projects\Yuki`): скопируй всю папку целиком (включая `.venv`, `.venv-sing`, `.venv-tts`,
  `data`, `singer\seed-vc`, `desktop\node_modules`) и работай уже там.
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
- Коммиты заканчивать строкой `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
