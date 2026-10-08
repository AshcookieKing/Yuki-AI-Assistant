// Микрофон: детектор речи по энергии с адаптивным порогом шума + режим «по кнопке».
const SR = 16000;
const FRAME = 1024; // 64 мс

export class Mic {
  constructor({ onSpeech, onLevel, onStart }) {
    this.onSpeech = onSpeech;
    this.onLevel = onLevel || (() => {});
    this.onStart = onStart || (() => {});
    this.mode = 'vad';
    this.sensitivity = 0.55;
    this.silenceMs = 800;
    this.paused = false;
    this.ducking = false; // она сейчас говорит: слушаем с повышенным порогом, чтобы не ловить её голос из колонок
    this.noise = 0.004;
    this.speaking = false;
    this.frames = [];
    this.preroll = [];
    this.loud = 0;
    this.quietMs = 0;
    this.ptt = false;
  }

  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    this.ctx = new AudioContext({ sampleRate: SR });
    const src = this.ctx.createMediaStreamSource(this.stream);
    const proc = this.ctx.createScriptProcessor(FRAME, 1, 1);
    proc.onaudioprocess = (e) => this.process(new Float32Array(e.inputBuffer.getChannelData(0)));
    src.connect(proc);
    proc.connect(this.ctx.destination); // нужно, чтобы обработчик вызывался; выход — тишина
  }

  configure(s) {
    this.mode = s.listen_mode;
    this.sensitivity = +s.vad_sensitivity;
    this.silenceMs = +s.silence_ms;
  }

  pttDown() { if (this.mode !== 'ptt' || this.ptt) return; this.ptt = true; this.frames = []; this.onStart(); }
  pttUp() {
    if (!this.ptt) return;
    this.ptt = false;
    this.emit();
  }

  emit() {
    const total = this.frames.length * FRAME;
    if (total >= SR * 0.35) {
      const out = new Float32Array(total);
      this.frames.forEach((f, i) => out.set(f, i * FRAME));
      this.onSpeech(out);
    }
    this.frames = [];
  }

  process(buf) {
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    const rms = Math.sqrt(sum / buf.length);
    this.onLevel(Math.min(1, rms * 12));

    if (this.mode === 'ptt') {
      if (this.ptt) this.frames.push(buf);
      return;
    }
    if (this.mode !== 'vad' || this.paused) {
      if (this.speaking) { this.speaking = false; this.frames = []; }
      return;
    }

    let thr = this.noise * (1.8 + (1 - this.sensitivity) * 6) + 0.002 + (1 - this.sensitivity) * 0.01;
    if (this.ducking) thr = thr * 2.6 + 0.012;
    const frameMs = (FRAME / SR) * 1000;

    if (!this.speaking) {
      // фон обновляем только в тишине (и не пока она говорит — иначе её голос станет «фоном»)
      if (!this.ducking) this.noise = this.noise * 0.97 + Math.min(rms, this.noise * 3 + 0.001) * 0.03;
      this.preroll.push(buf);
      if (this.preroll.length > 5) this.preroll.shift();
      this.loud = rms > thr ? this.loud + 1 : 0;
      if (this.loud >= (this.ducking ? 5 : 3)) {
        this.speaking = true;
        this.quietMs = 0;
        this.frames = [...this.preroll];
        this.onStart();
      }
    } else {
      this.frames.push(buf);
      this.quietMs = rms > thr * 0.8 ? 0 : this.quietMs + frameMs;
      const tooLong = this.frames.length * frameMs > 30000;
      if (this.quietMs >= this.silenceMs || tooLong) {
        this.speaking = false;
        this.loud = 0;
        // отрезаем хвост тишины
        const drop = Math.floor(this.quietMs / frameMs) - 3;
        if (drop > 0) this.frames.splice(-drop);
        this.emit();
      }
    }
  }
}

export function toWav(samples, sr = SR) {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const w = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  w(0, 'RIFF'); v.setUint32(4, 36 + samples.length * 2, true); w(8, 'WAVE');
  w(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, sr, true); v.setUint32(28, sr * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  w(36, 'data'); v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buf], { type: 'audio/wav' });
}
