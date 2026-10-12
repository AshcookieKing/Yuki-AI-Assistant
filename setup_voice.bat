@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Установка голосовых модулей Юки
echo ================================================
echo   Пение и нейро-голос (нужна видеокарта NVIDIA, ~6 ГБ)
echo ================================================

set PY=
for %%v in (3.11 3.12) do (
  if not defined PY ( py -%%v -c "import venv" >nul 2>&1 && set PY=py -%%v )
)
if not defined PY ( echo [!] Сначала запусти setup.bat & pause & exit /b 1 )

rem ---------- 1. Нейро-голос Chatterbox (живой голос по образцу) ----------
echo.
echo [1/2] Нейро-голос Chatterbox...
if not exist .venv-tts ( %PY% -m venv .venv-tts )
.venv-tts\Scripts\python.exe -m pip install --upgrade pip
.venv-tts\Scripts\python.exe -m pip install chatterbox-tts
.venv-tts\Scripts\python.exe -m pip install torch==2.6.0+cu124 torchaudio==2.6.0+cu124 --force-reinstall --no-deps --index-url https://download.pytorch.org/whl/cu124

rem ---------- 2. Пение: Seed-VC + отделение вокала ----------
echo.
echo [2/2] Пение (Seed-VC, Demucs)...
if not exist singer\seed-vc (
  curl -L -o singer\seed-vc.zip https://codeload.github.com/Plachtaa/seed-vc/zip/refs/heads/main
  tar -xf singer\seed-vc.zip -C singer
  ren singer\seed-vc-main seed-vc
  del singer\seed-vc.zip
)
if not exist .venv-sing ( %PY% -m venv .venv-sing )
set P=.venv-sing\Scripts\python.exe
%P% -m pip install --upgrade pip
%P% -m pip install torch==2.4.0 torchaudio==2.4.0 --index-url https://download.pytorch.org/whl/cu121
%P% -m pip install numpy==1.26.4 scipy==1.13.1 librosa==0.10.2 "huggingface-hub>=0.28.1" munch==4.0.0 einops==0.8.0 descript-audio-codec==1.0.0 transformers==4.46.3 soundfile hydra-core==1.3.2 pyyaml pydub accelerate demucs yt-dlp imageio-ffmpeg

echo.
echo Готово! В настройках Юки: Голос → Движок → «Нейро-голос Chatterbox».
pause
