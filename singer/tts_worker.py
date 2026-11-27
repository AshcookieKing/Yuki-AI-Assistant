"""Нейро-голос в отдельном процессе (.venv-tts, HTTP 127.0.0.1:8767). Два движка, оба бесплатные и офлайн:
- espeech — ESpeech-TTS-1 RL-V2 (F5-TTS, дообучен на русском, Apache 2.0): живой русский, ударения через RUAccent,
  клонирует тембр образца за секунды — основной;
- chatterbox — Chatterbox Multilingual (resemble-ai, MIT): эмоциональнее, но медленнее.
Модели загружаются один раз и держатся в памяти."""
import io
import json
import re
import sys
import threading
import time
import traceback
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

_lock = threading.Lock()
_cb = None
_f5 = {}
_ref_cache = {}

F5_REPO = "ESpeech/ESpeech-TTS-1_RL-V2"
F5_CFG = dict(dim=1024, depth=22, heads=16, ff_mult=2, text_dim=512, conv_layers=4)


def chatterbox():
    global _cb
    if _cb is None:
        import torch
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS

        _cb = ChatterboxMultilingualTTS.from_pretrained(device="cuda" if torch.cuda.is_available() else "cpu")
    return _cb


def f5():
    if not _f5:
        from f5_tts.infer.utils_infer import load_model, load_vocoder
        from f5_tts.model import DiT
        from huggingface_hub import hf_hub_download
        from ruaccent import RUAccent

        import torch

        dev = "cuda" if torch.cuda.is_available() else "cpu"
        ckpt = hf_hub_download(F5_REPO, "espeech_tts_rlv2.pt")
        vocab = hf_hub_download(F5_REPO, "vocab.txt")
        _f5["model"] = load_model(DiT, F5_CFG, ckpt, vocab_file=vocab, device=dev)
        _f5["vocoder"] = load_vocoder(device=dev)
        _f5["device"] = dev
        acc = RUAccent()
        acc.load(omograph_model_size="turbo3.1", use_dictionary=True, tiny_mode=False)
        _f5["acc"] = acc
    return _f5


def _accent(text):
    try:
        return f5()["acc"].process_all(text)
    except Exception:
        return text


def _ref(ref, ref_text):
    """Образец обрабатывается один раз (обрезка тишины, ударения) и кэшируется."""
    key = (ref, ref_text)
    if key not in _ref_cache:
        from f5_tts.infer.utils_infer import preprocess_ref_audio_text

        _ref_cache[key] = preprocess_ref_audio_text(ref, _accent(ref_text))
    return _ref_cache[key]


def _wav(audio, sr):
    peak = float(np.abs(audio).max()) or 1.0
    pcm = (np.clip(audio / max(peak, 0.9) * 0.95, -1, 1) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def synth_f5(text, ref, ref_text, nfe, speed):
    from f5_tts.infer.utils_infer import infer_process

    with _lock:
        m = f5()
        ra, rt = _ref(ref, ref_text)
        gen = _accent(text)
        # F5 плохо говорит очень короткие фразы — добавляем паузу-точку
        if len(re.sub(r"\W", "", gen)) < 6:
            gen = gen.rstrip(".!?…") + "..."
        audio, sr, _ = infer_process(ra, rt, gen, m["model"], m["vocoder"], nfe_step=int(nfe), speed=float(speed),
                                     cross_fade_duration=0.12, show_info=lambda *a, **k: None, device=m["device"])
    return _wav(np.asarray(audio, np.float32), sr)


def synth_cb(text, ref, exaggeration, cfg, temperature):
    with _lock:
        m = chatterbox()
        wav = m.generate(text, language_id="ru", audio_prompt_path=ref, exaggeration=exaggeration,
                         cfg_weight=cfg, temperature=temperature)
        audio = wav.squeeze().detach().cpu().numpy().astype(np.float32)
        sr = m.sr
    return _wav(audio, sr)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        engine = body.get("engine", "chatterbox")
        try:
            if self.path == "/warmup":
                if engine == "espeech":
                    f5()
                    if body.get("ref") and body.get("ref_text"):
                        synth_f5("Привет!", body["ref"], body["ref_text"], 8, 1.0)
                else:
                    chatterbox()
                data, ctype = b'{"ok": true}', "application/json"
            else:
                t = time.time()
                if engine == "espeech":
                    data = synth_f5(body["text"], body["ref"], body.get("ref_text", ""),
                                    body.get("nfe", 16), body.get("speed", 1.0))
                else:
                    data = synth_cb(body["text"], body["ref"], float(body.get("exaggeration", 0.7)),
                                    float(body.get("cfg", 0.35)), float(body.get("temperature", 0.8)))
                ctype = "audio/wav"
                print(f"[tts:{engine}] {len(body['text'])} симв. за {time.time() - t:.2f} с", flush=True)
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
