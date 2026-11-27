"""Голос Qwen3-TTS CustomVoice (Qwen, Apache 2.0, офлайн): готовый тембр Ono_Anna — игривый, лёгкий девичий голос.
Модель 1.7B понимает инструкцию манеры («игриво, с хитринкой»), 0.6B — легче и быстрее, но без инструкций.
Отдельное окружение .venv-qtts (нужна своя версия transformers), HTTP 127.0.0.1:8768."""
import io
import json
import sys
import threading
import time
import traceback
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

_lock = threading.Lock()
_models = {}

REPOS = {"0.6b": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice", "1.7b": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"}


def model(size):
    if size not in _models:
        import torch
        from qwen_tts import Qwen3TTSModel

        cuda = torch.cuda.is_available()
        _models.clear()  # держим в памяти только одну модель
        _models[size] = Qwen3TTSModel.from_pretrained(
            REPOS[size], device_map="cuda:0" if cuda else "cpu",
            dtype=torch.bfloat16 if cuda else torch.float32, attn_implementation="sdpa")
    return _models[size]


def _wav(audio, sr):
    audio = np.asarray(audio, np.float32).reshape(-1)
    peak = float(np.abs(audio).max()) or 1.0
    pcm = (np.clip(audio / max(peak, 0.9) * 0.95, -1, 1) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def synth(text, size, speaker, instruct, language):
    with _lock:
        m = model(size)
        kw = {"text": text, "language": language, "speaker": speaker}
        if instruct and size != "0.6b":
            kw["instruct"] = instruct
        wavs, sr = m.generate_custom_voice(**kw)
    return _wav(wavs[0], sr)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        size = body.get("size", "1.7b")
        try:
            if self.path == "/warmup":
                synth("Привет!", size, body.get("speaker", "Ono_Anna"), body.get("instruct", ""), "Russian")
                data, ctype = b'{"ok": true}', "application/json"
            else:
                t = time.time()
                data = synth(body["text"], size, body.get("speaker", "Ono_Anna"), body.get("instruct", ""),
                             body.get("language", "Russian"))
                ctype = "audio/wav"
                print(f"[qtts:{size}] {len(body['text'])} симв. за {time.time() - t:.2f} с", flush=True)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            traceback.print_exc()
            msg = json.dumps({"error": str(e)}).encode()
            self.send_response(500)
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)


if __name__ == "__main__":
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 8768), Handler)
    except OSError:
        print("[qtts] уже запущен — выхожу", flush=True)
        sys.exit(0)
    print("[qtts] listening on 8768", flush=True)
    server.serve_forever()
