// Липсинк: заранее считаем по всей фразе громкость и «тип» звука для каждого кадра 16 мс,
// а при проигрывании берём кадр по точному времени звука — без задержки анализатора.
const HOP = 0.016;

export function analyzeSpeech(buffer) {
  const data = buffer.getChannelData(0);
  const sr = buffer.sampleRate;
  const hop = Math.max(1, Math.round(sr * HOP));
  const n = Math.floor(data.length / hop);
  const rms = new Float32Array(n);
  const lowRatio = new Float32Array(n);
  const zcr = new Float32Array(n);

  // однополюсный ФНЧ ~900 Гц: доля низких частот отличает «о/у» от «и/е»
  const a = Math.exp(-2 * Math.PI * 900 / sr);
  let lp = 0;
  for (let f = 0; f < n; f++) {
    let e = 0, el = 0, zc = 0, prev = data[f * hop];
    for (let i = f * hop; i < (f + 1) * hop; i++) {
      const x = data[i];
      lp = a * lp + (1 - a) * x;
      e += x * x;
      el += lp * lp;
      if ((x >= 0) !== (prev >= 0)) zc++;
      prev = x;
    }
    rms[f] = Math.sqrt(e / hop);
    lowRatio[f] = e > 1e-9 ? el / e : 0;
    zcr[f] = zc / hop;
  }

  // нормируем громкость по самой фразе, чтобы рот открывался одинаково при любой громкости
  const sorted = Array.from(rms).sort((x, y) => x - y);
  const ref = sorted[Math.floor(sorted.length * 0.9)] || 1e-3;
  const gate = ref * 0.12;
  const level = new Float32Array(n);
  const vowel = new Array(n);
  for (let f = 0; f < n; f++) {
    level[f] = Math.min(1, Math.max(0, (rms[f] - gate) / (ref - gate)));
    const lr = lowRatio[f];
    if (zcr[f] > 0.25) vowel[f] = 'ih';                  // шипящие/свистящие — рот почти закрыт, зубы
    else if (lr > 0.82) vowel[f] = level[f] < 0.5 ? 'ou' : 'oh';
    else if (lr < 0.45) vowel[f] = 'ee';
    else vowel[f] = 'aa';
  }
  // сглаживаем форму рта: берём самую частую гласную в окне ~100 мс, чтобы не дёргалась
  const smooth = new Array(n);
  for (let f = 0; f < n; f++) {
    const cnt = {};
    for (let j = Math.max(0, f - 3); j <= Math.min(n - 1, f + 3); j++) {
      if (level[j] > 0.05) cnt[vowel[j]] = (cnt[vowel[j]] || 0) + level[j];
    }
    let best = vowel[f], bv = -1;
    for (const k in cnt) if (cnt[k] > bv) { bv = cnt[k]; best = k; }
    smooth[f] = best;
  }
  return { level, vowel: smooth, n };
}

export function mouthAt(lip, t) {
  if (t < 0) return [0, 'aa'];
  const f = t / HOP;
  const i = Math.floor(f);
  if (i >= lip.n - 1) return [0, 'aa'];
  const k = f - i;
  let lvl = lip.level[i] * (1 - k) + lip.level[i + 1] * k;
  if (lip.vowel[i] === 'ih') lvl *= 0.5;
  // лёгкое «пережатие», чтобы рот открывался выразительнее
  return [Math.min(1, Math.pow(lvl, 0.8) * 1.05), lip.vowel[i]];
}
