@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Голосовой модуль: живой голос по образцу и пение (≈4 ГБ) ===
if not exist singer\seed-vc ( git clone --depth 1 https://github.com/Plachtaa/seed-vc singer\seed-vc )
if not exist .venv-sing ( py -3.11 -m venv .venv-sing )
set P=.venv-sing\Scripts\python.exe
%P% -m pip install --upgrade pip
%P% -m pip install torch==2.4.0 torchaudio==2.4.0 --index-url https://download.pytorch.org/whl/cu121
%P% -m pip install numpy==1.26.4 scipy==1.13.1 librosa==0.10.2 "huggingface-hub>=0.28.1" munch==4.0.0 einops==0.8.0 descript-audio-codec==1.0.0 transformers==4.46.3 soundfile hydra-core==1.3.2 pyyaml pydub accelerate demucs yt-dlp imageio-ffmpeg
echo Готово!
pause
