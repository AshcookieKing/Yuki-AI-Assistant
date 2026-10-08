"""Голосовой процесс (запускается из .venv-sing с CUDA):
- /convert — перенести голос фразы на образец (речь, 22 кГц, быстро);
- /sing    — кавер: скачать песню, отделить вокал (Demucs), перепеть голосом образца (Seed-VC, 44 кГц), смешать.
Модели держатся в памяти, сервер ассистента общается с ним по HTTP на 127.0.0.1:8766."""
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = HERE / "seed-vc"
ROOT = HERE.parent
SONGS = ROOT / "data" / "songs"
SONGS.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(SEED))
os.chdir(SEED)  # Seed-VC ищет конфиги относительно своей папки

import imageio_ffmpeg  # noqa: E402
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
os.environ["PATH"] = str(Path(FFMPEG).parent) + os.pathsep + os.environ["PATH"]

from pydub import AudioSegment  # noqa: E402

AudioSegment.converter = FFMPEG

_vc = None
_vc_lock = threading.Lock()
progress = {"stage": "idle", "detail": ""}


def vc():
    global _vc
    if _vc is None:
        from seed_vc_wrapper import SeedVCWrapper

        progress.update(stage="loading", detail="Загружаю голосовые модели")
        _vc = SeedVCWrapper()
    return _vc


def run_vc(source, target, f0, steps, shift=0, auto_f0=False):
    with _vc_lock:
        gen = vc().convert_voice(source, target, diffusion_steps=steps, f0_condition=f0,
                                 auto_f0_adjust=auto_f0, pitch_shift=shift, stream_output=True)
        full, sr = None, (44100 if f0 else 22050)
        for _mp3, audio in gen:
            if audio is not None:
                full = audio
        if isinstance(full, tuple):  # обёртка отдаёт (частота, массив)
            sr, full = full
        return np.asarray(full, dtype=np.float32), sr


def median_f0(path, sr=16000):
    import librosa

    y, _ = librosa.load(path, sr=sr, mono=True, duration=60)
    f0 = librosa.yin(y, fmin=70, fmax=900, sr=sr)
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
    n = min(len(f0), len(rms))
    voiced = f0[:n][rms[:n] > np.percentile(rms[:n], 60)]
    return float(np.median(voiced)) if len(voiced) else 0.0


def vocal_chain(reverb: float) -> str:
    """Обработка вокала как на сцене: срез низа, компрессор, «воздух», стерео-эхо и хвост реверберации."""
    r = max(0.0, min(1.0, float(reverb)))
    chain = ("[1:a]aresample=44100,highpass=f=100,"
             "acompressor=threshold=-20dB:ratio=3.5:attack=8:release=150:makeup=3,"
             "equalizer=f=3500:t=q:w=1.2:g=2.5,equalizer=f=250:t=q:w=1:g=-2,"
             "volume=1.2,pan=stereo|c0=c0|c1=c0")
    if r > 0:
        # несколько отражений разной длины = зал; левый и правый канал чуть разные — объём
        chain += (f",aecho=0.85:0.8:70|140|230|360:{0.32 * r:.2f}|{0.24 * r:.2f}|{0.17 * r:.2f}|{0.11 * r:.2f},"
                  f"aecho=0.9:0.7:45|510:{0.14 * r:.2f}|{0.08 * r:.2f}")
    return chain + "[b];"


def sing(query, target, max_seconds, reverb=0.5, steps=30):
    key = hashlib.md5(f"{query}|{Path(target).stat().st_mtime}|{max_seconds}|{reverb}|{steps}".encode()).hexdigest()[:12]
    out = SONGS / key
    mix, vocals = out / "mix.wav", out / "vocals.wav"
    if mix.exists():
        return {"mix": str(mix), "vocals": str(vocals), "title": (out / "title.txt").read_text("utf-8")}
    out.mkdir(exist_ok=True)

    # 1. скачиваем аудио первой подходящей песни
    progress.update(stage="download", detail=f"Ищу «{query}»")
    import yt_dlp

    opts = {"format": "bestaudio/best", "outtmpl": str(out / "src.%(ext)s"), "noplaylist": True, "quiet": True,
            "ffmpeg_location": FFMPEG, "match_filter": yt_dlp.utils.match_filter_func("duration < 600"),
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}]}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch1:{query}", download=True)
        entry = info["entries"][0] if "entries" in info else info
    title = entry.get("title", query)
    (out / "title.txt").write_text(title, "utf-8")
    src = out / "src.wav"

    # обрезаем до max_seconds, начиная немного позже начала (пропускаем интро)
    start = 0 if (entry.get("duration") or 0) < max_seconds + 30 else 15
    cut = out / "cut.wav"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-ss", str(start), "-t", str(max_seconds), "-i", str(src),
                    "-ar", "44100", "-ac", "2", str(cut)], check=True)

    # 2. отделяем вокал от музыки
    progress.update(stage="separate", detail="Отделяю вокал от музыки")
    subprocess.run([sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs_ft",
                    "-o", str(out / "sep"), str(cut)], check=True, capture_output=True)
    sep = out / "sep" / "htdemucs_ft" / "cut"
    voc_src, inst = sep / "vocals.wav", sep / "no_vocals.wav"

    # 3. перепеваем своим голосом; мужской вокал поднимаем на октаву (тональность песни не меняется)
    progress.update(stage="convert", detail="Перепеваю своим голосом")
    src_f0, tgt_f0 = median_f0(str(voc_src)), median_f0(target)
    shift = 12 if src_f0 and tgt_f0 and tgt_f0 / src_f0 > 1.5 else (-12 if src_f0 and tgt_f0 / src_f0 < 0.6 else 0)
    audio, sr = run_vc(str(voc_src), target, f0=True, steps=steps, shift=shift)
    sf.write(vocals, audio, sr)

    # 4. сводим: вокал + минус
    progress.update(stage="mix", detail="Свожу трек")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(inst), "-i", str(vocals), "-filter_complex",
                    "[0:a]volume=0.8[a];" + vocal_chain(reverb) +
                    "[a][b]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95",
                    "-ar", "44100", str(mix)], check=True)
    for f in (src, cut):
        f.unlink(missing_ok=True)
    return {"mix": str(mix), "vocals": str(vocals), "title": title}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/progress":
            self._send(200, progress)
        else:
            self._send(200, {"ok": True})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        try:
            if self.path == "/convert":
                t = time.time()
                audio, sr = run_vc(body["source"], body["target"], f0=bool(body.get("f0", False)),
                                   steps=int(body.get("steps", 10)), shift=int(body.get("shift", 0)),
                                   auto_f0=bool(body.get("auto_f0", True)))
                sf.write(body["output"], audio, sr)
                self._send(200, {"output": body["output"], "seconds": round(time.time() - t, 2)})
            elif self.path == "/sing":
                res = sing(body["query"], body["target"], int(body.get("max_seconds", 90)),
                           float(body.get("reverb", 0.5)), int(body.get("steps", 30)))
                progress.update(stage="idle", detail="")
                self._send(200, res)
            elif self.path == "/warmup":
                vc()
                progress.update(stage="idle", detail="")
                self._send(200, {"ok": True})
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:
            traceback.print_exc()
            progress.update(stage="idle", detail="")
            self._send(500, {"error": str(e)})


if __name__ == "__main__":
    print("[voice-worker] listening on 8766", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8766), Handler).serve_forever()
