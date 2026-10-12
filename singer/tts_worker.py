"""Нейро-голос Chatterbox Multilingual (resemble-ai/chatterbox, MIT): озвучивает русский текст тембром образца
с регулируемой эмоциональностью. Работает в отдельном окружении .venv-tts, сервер ассистента обращается
к нему по HTTP на 127.0.0.1:8767. Модель загружается один раз и держится в памяти."""
import io
import json
import sys
import threading
import time
import traceback
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

_model = None
_lock = threading.Lock()


def model():
    global _model
    if _model is None:
        import torch
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS

        _model = ChatterboxMultilingualTTS.from_pretrained(device="cuda" if torch.cuda.is_available() else "cpu")
    return _model


def synth(text, ref, exaggeration, cfg, temperature):
    with _lock:
        m = model()
        wav = m.generate(text, language_id="ru", audio_prompt_path=ref, exaggeration=exaggeration,
                         cfg_weight=cfg, temperature=temperature)
        audio = wav.squeeze().detach().cpu().numpy().astype(np.float32)
        sr = m.sr
    peak = float(np.abs(audio).max()) or 1.0
    pcm = (np.clip(audio / max(peak, 0.9) * 0.95, -1, 1) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        try:
            if self.path == "/warmup":
                model()
                data, ctype = b'{"ok": true}', "application/json"
            else:
                t = time.time()
                data = synth(body["text"], body["ref"], float(body.get("exaggeration", 0.7)),
                             float(body.get("cfg", 0.35)), float(body.get("temperature", 0.8)))
                ctype = "audio/wav"
                print(f"[tts] {len(body['text'])} симв. за {time.time() - t:.2f} с", flush=True)
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
        server = ThreadingHTTPServer(("127.0.0.1", 8767), Handler)
    except OSError:
        print("[tts] уже запущен — выхожу", flush=True)
        sys.exit(0)
    print("[tts] listening on 8767", flush=True)
    server.serve_forever()
