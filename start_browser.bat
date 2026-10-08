@echo off
chcp 65001 >nul
cd /d "%~dp0"
set OLLAMA="%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
powershell -NoProfile -Command "try { Invoke-WebRequest http://127.0.0.1:11434/api/version -UseBasicParsing -TimeoutSec 2 | Out-Null } catch { exit 1 }"
if errorlevel 1 ( start "" /min %OLLAMA% serve )

start "Yuki server" /min .venv\Scripts\python.exe server.py
echo Жду запуска сервера...
:wait
timeout /t 1 >nul
powershell -NoProfile -Command "try { Invoke-WebRequest http://127.0.0.1:8765/api/status -UseBasicParsing -TimeoutSec 2 | Out-Null } catch { exit 1 }"
if errorlevel 1 goto wait

set EDGE="%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if exist %EDGE% (
  start "" %EDGE% --app=http://127.0.0.1:8765 --window-size=1100,900 --autoplay-policy=no-user-gesture-required
) else (
  start "" http://127.0.0.1:8765
)
