@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Установка ИИ-ассистента Юки
echo.
echo ================================================
echo     Установка ИИ-ассистента Юки (шаг 1 из 2)
echo ================================================
echo.

rem ---------- 1. Python ----------
set PY=
for %%v in (3.11 3.12 3.13) do (
  if not defined PY ( py -%%v -c "import venv" >nul 2>&1 && set PY=py -%%v )
)
if not defined PY (
  echo [!] Не найден Python 3.11.
  echo     Скачай: https://www.python.org/downloads/release/python-3119/
  echo     При установке ОБЯЗАТЕЛЬНО поставь галочку "Add python.exe to PATH".
  start https://www.python.org/downloads/release/python-3119/
  pause & exit /b 1
)
echo [ok] Python: %PY%

rem ---------- 2. Node.js (нужен только при установке из исходников) ----------
if defined YUKI_PACKAGED goto skip_node
where npm >nul 2>&1
if errorlevel 1 (
  echo [!] Не найден Node.js. Скачай версию LTS: https://nodejs.org/
  start https://nodejs.org/
  pause & exit /b 1
)
echo [ok] Node.js
:skip_node

rem ---------- 3. Ollama ----------
set OLLAMA="%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
if not exist %OLLAMA% (
  echo [!] Не найдена Ollama — это «мозг» ассистента.
  echo     Скачай и установи: https://ollama.com/download , потом запусти setup.bat снова.
  start https://ollama.com/download
  pause & exit /b 1
)
echo [ok] Ollama
echo.

rem ---------- 4. Библиотеки Python ----------
echo Ставлю библиотеки Python (5-15 минут, нужен интернет)...
if not exist .venv ( %PY% -m venv .venv )
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements.txt
if errorlevel 1 ( echo [!] Не удалось поставить библиотеки. Проверь интернет и запусти снова. & pause & exit /b 1 )

rem ---------- 5. 3D-модель ----------
if not exist static\models\default.vrm (
  echo Скачиваю 3D-модель...
  curl -L -o static\models\default.vrm https://pixiv.github.io/three-vrm/packages/three-vrm/examples/models/VRM1_Constraint_Twist_Sample.vrm
)

rem ---------- 6. Модели ИИ ----------
echo Запускаю Ollama и скачиваю мозг (~7 ГБ)...
powershell -NoProfile -Command "try { Invoke-WebRequest http://127.0.0.1:11434/api/version -UseBasicParsing -TimeoutSec 2 | Out-Null } catch { exit 1 }"
if errorlevel 1 ( start "" /min %OLLAMA% serve & timeout /t 5 >nul )
%OLLAMA% pull qwen3.5:9b
%OLLAMA% pull embeddinggemma:300m

if not exist data mkdir data
echo ok> data\.installed
if defined YUKI_PACKAGED goto done

rem ---------- 7. Оболочка фигурки ----------
echo Ставлю оболочку фигурки (Electron)...
pushd desktop & call npm install --no-audit --no-fund & popd

rem ---------- 8. Ярлык на рабочем столе ----------
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Юки.lnk'); $s.TargetPath='%~dp0start.bat'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0desktop\icon.ico'; $s.WindowStyle=7; $s.Save()"


:done
echo.
echo ================================================
echo  Готово! Запускай «Юки» с рабочего стола.
echo  Пение и голос по образцу: запусти setup_voice.bat
echo  (необязательно, ещё ~5 ГБ, нужна видеокарта NVIDIA).
echo ================================================
pause
