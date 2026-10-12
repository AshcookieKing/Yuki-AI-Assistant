// Режимы: mascot — фигурка на рабочем столе (звук + микрофон + 3D, без интерфейса),
//         panel  — окно настроек и чата (без 3D и звука),
//         full   — всё в одном окне браузера.
import { Avatar } from './avatar.js';
import { Mic, toWav } from './mic.js';
import { analyzeSpeech, mouthAt } from './lipsync.js';

const MODE = new URLSearchParams(location.search).get('mode') || 'full';
const SPEAKER = MODE !== 'panel';            // это окно говорит и слушает
const desktop = window.desktop || null;       // мост Electron (есть только в приложении)
document.body.classList.add('mode-' + MODE);

const $ = (id) => document.getElementById(id);
const EMO_RE = /\[(happy|sad|angry|surprised|relaxed|neutral|shy|think)\]/gi;

let S = {};
let META = {};
let ws;
let busy = false;
let lastActivity = Date.now();
let idleCount = 0;
let mic = null;

const avatar = SPEAKER ? new Avatar($('scene'), { mascot: MODE === 'mascot' }) : null;
window.avatar = avatar; // для отладки из консоли

// ---------- Аудио: очередь фраз + точный липсинк по заранее посчитанной огибающей ----------
const audio = {
  ctx: null, gain: null, queue: [], playing: false, current: null, doneFlag: true,
  init() {
    this.ctx = new AudioContext();
    this.gain = this.ctx.createGain();
    this.gain.gain.value = +S.volume || 1;
    this.gain.connect(this.ctx.destination);
    if (avatar) avatar.mouthSource = () => this.mouth();
  },
  enqueue(item) {
    // декодируем сразу, но играем строго по порядку прихода
    let raw = item.raw;
    if (!raw && item.audio) raw = Uint8Array.from(atob(item.audio), (c) => c.charCodeAt(0)).buffer;
    item.ready = raw
      ? this.ctx.decodeAudioData(raw).then((b) => { item.buffer = b; item.lip = analyzeSpeech(b); }, () => {})
      : Promise.resolve();
    if (item.lipRaw) {
      // для песни рот двигаем по чистому вокалу, а играем трек с музыкой
      item.ready = Promise.all([item.ready, this.ctx.decodeAudioData(item.lipRaw)
        .then((v) => { item.lip = analyzeSpeech(v); }, () => {})]);
    }
    this.queue.push(item);
    if (!this.playing) this.next();
  },
  async next() {
    const item = this.queue.shift();
    if (!item) {
      this.playing = false;
      if (avatar) avatar.speaking = false;
      hideSubtitle(1500);
      setState(this.doneFlag ? 'idle' : 'thinking');
      return;
    }
    this.playing = true;
    await item.ready;
    if (!this.playing) return; // остановили, пока декодировали
    if (avatar) {
      avatar.speaking = true;
      if (item.emotion) avatar.setEmotion(item.emotion, 8);
      if (item.gesture) avatar.gesture(item.gesture);
      if (item.song) { avatar.gesture('sing'); avatar.gestureUntil = avatar.t + item.buffer.duration; }
    }
    setState('speaking');
    showSubtitle(item.text);
    if (!item.buffer) { setTimeout(() => this.next(), Math.min(4000, 60 * item.text.length)); return; }
    const src = this.ctx.createBufferSource();
    src.buffer = item.buffer;
    src.connect(this.gain);
    src.onended = () => { if (this.current === src) { this.current = null; this.next(); } };
    this.current = src;
    this.lip = item.lip;
    this.startAt = this.ctx.currentTime + 0.02;
    src.start(this.startAt);
  },
  mouth() {
    if (!this.current || !this.lip) return [0, 'aa'];
    // что слышно прямо сейчас: текущее время минус задержка вывода звука
    const t = this.ctx.currentTime - this.startAt - (this.ctx.outputLatency || this.ctx.baseLatency || 0);
    return mouthAt(this.lip, t);
  },
  stop() {
    this.queue = [];
    if (this.current) { const c = this.current; this.current = null; try { c.stop(); } catch {} }
    this.playing = false;
    if (avatar) avatar.speaking = false;
  },
};

// ---------- Состояние ----------
const STATE_TEXT = { idle: 'ждёт', listening: 'слушает', thinking: 'думает', speaking: 'говорит', sleep: 'сплю' };
function setState(st) {
  const el = $('stateLabel');
  el.textContent = STATE_TEXT[st] || st;
  el.className = 'pill ' + st;
  document.body.dataset.state = st;
  if (avatar) avatar.listening = st === 'listening';
  if (st === 'idle') {
    busy = false;
    lastActivity = Date.now();
  }
  if (mic) {
    // перебивать можно всегда, кроме случая, когда это отключено в настройках
    mic.paused = !S.barge_in && (st === 'speaking' || st === 'thinking');
    mic.ducking = st === 'speaking';
  }
}

let subTimer;
function showSubtitle(text) {
  clearTimeout(subTimer);
  if (!S.show_subtitles || !text) return;
  $('subtitle').textContent = text;
  $('subtitle').classList.remove('hidden');
}
function hideSubtitle(delay = 0) {
  clearTimeout(subTimer);
  subTimer = setTimeout(() => $('subtitle').classList.add('hidden'), delay);
}

let botMsg = null;
function addMsg(cls, text) {
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  d.textContent = text;
  $('logInner').appendChild(d);
  $('log').scrollTop = $('log').scrollHeight;
  return d;
}

// ---------- WebSocket ----------
function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (e) => handle(JSON.parse(e.data));
  ws.onclose = () => setTimeout(connect, 1500);
}
const send = (msg) => { if (ws?.readyState === 1) ws.send(JSON.stringify(msg)); };

function handle(m) {
  switch (m.type) {
    case 'user':
      // реплика пользователя из любого окна
      if (!m.hidden) addMsg('user', m.text);
      botMsg = null;
      busy = true;
      audio.doneFlag = false;
      if (SPEAKER) { audio.stop(); unduck(); avatar?.setEmotion('think', 30); }
      setState('thinking');
      break;
    case 'delta':
      if (!botMsg) botMsg = addMsg('bot', '');
      botMsg.dataset.raw = (botMsg.dataset.raw || '') + m.text;
      botMsg.textContent = botMsg.dataset.raw.replace(EMO_RE, '').trim();
      $('log').scrollTop = $('log').scrollHeight;
      break;
    case 'speech':
      if (SPEAKER && audio.ctx) audio.enqueue(m);
      break;
    case 'emotion':
      avatar?.setEmotion(m.emotion, 5);
      if (m.gesture) avatar?.gesture(m.gesture);
      break;
    case 'tool':
      botMsg = null;
      addMsg('tool', `⚙ ${m.name} ${JSON.stringify(m.args)}`);
      avatar?.setEmotion('think', 10);
      break;
    case 'tool_result':
      addMsg('tool', `→ ${m.result}`);
      break;
    case 'confirm_request':
      $('confirmName').textContent = m.name;
      $('confirmDetail').textContent = m.detail;
      $('confirm').classList.remove('hidden');
      $('confirm').dataset.id = m.id;
      desktop?.setIgnore(false);
      break;
    case 'confirm_done':
      if ($('confirm').dataset.id === m.id) $('confirm').classList.add('hidden');
      break;
    case 'error':
      addMsg('err', m.text);
      avatar?.setEmotion('sad', 4);
      break;
    case 'done':
      botMsg = null;
      audio.doneFlag = true;
      if (!audio.playing) setState('idle');
      break;
    case 'ignored':
      // фраза не ей (фон, видео, разговор с другими) — молча пропускаем и возвращаем громкость
      addMsg('tool', '🙉 не мне: ' + m.text);
      unduck();
      if (!busy) setState('idle');
      break;
    case 'memory':
      addMsg('tool', '🧠 Запомнила: ' + m.items.join('; '));
      if (!$('settings').classList.contains('hidden')) loadMemory();
      break;
    case 'status':
      showSubtitle(m.text);
      avatar?.setEmotion('think', 6);
      break;
    case 'song':
      addMsg('tool', `🎵 ${m.title}`);
      if (SPEAKER && audio.ctx) playSong(m);
      break;
    case 'stopped':
      audio.stop();
      hideSubtitle();
      setState('idle');
      break;
    case 'cleared':
      $('logInner').innerHTML = '';
      break;
    case 'settings':
      Object.assign(S, m.patch);
      syncInputs();
      applyLive();
      break;
    case 'relay':
      if (m.data === 'toggle-mic' && SPEAKER) toggleMic();
      break;
  }
}

async function playSong(m) {
  const [mix, voc] = await Promise.all([m.mix, m.vocals].map((u) => fetch(u).then((r) => r.arrayBuffer())));
  audio.doneFlag = true;
  audio.enqueue({ raw: mix, lipRaw: voc, text: '🎵 ' + m.title, emotion: 'happy', song: true });
}

function unduck() {
  if (audio.ducked && audio.gain) {
    audio.gain.gain.setTargetAtTime(+S.volume || 1, audio.ctx.currentTime, 0.1);
    audio.ducked = false;
  }
}

function sendChat(text, hidden = false, voice = false) {
  text = text.trim();
  if (!text) return;
  if (!hidden) idleCount = 0;
  lastActivity = Date.now();
  send({ type: 'chat', text, hidden, voice });
}

function stopAll() {
  audio.stop();
  send({ type: 'stop' });
}

// ---------- Микрофон ----------
async function handleSpeech(samples) {
  setState('thinking');
  try {
    const r = await fetch('/api/stt', { method: 'POST', body: toWav(samples) });
    const { text, error } = await r.json();
    if (error) addMsg('err', 'Распознавание: ' + error);
    if (!text) { unduck(); if (!busy) setState('idle'); return; }
    sendChat(text, false, true);
  } catch (e) {
    addMsg('err', 'Ошибка распознавания: ' + e);
    setState('idle');
  }
}

function onVoiceStart() {
  lastActivity = Date.now();
  idleCount = 0;
  // услышала голос — сразу приглушается; замолчит совсем, если фраза окажется ей (сервер пришлёт 'user')
  if (S.barge_in && audio.playing && audio.gain) {
    audio.gain.gain.setTargetAtTime(0.15 * (+S.volume || 1), audio.ctx.currentTime, 0.05);
    audio.ducked = true;
  }
  if (!busy) setState('listening');
}

function toggleMic() {
  if (!SPEAKER) { send({ type: 'relay', data: 'toggle-mic' }); return; }
  const mode = S.listen_mode === 'off' ? 'vad' : 'off';
  save({ listen_mode: mode });
}

// ---------- Сама заговаривает при тишине ----------
setInterval(() => {
  const mins = +S.idle_chatter;
  if (!SPEAKER || !mins || busy || audio.playing || !ws) return;
  // чем дольше тишина, тем реже она заговаривает: 1x, 2x, 3x... интервала (максимум 6x)
  if (Date.now() - lastActivity > mins * 60000 * Math.min(6, idleCount + 1)) {
    idleCount++;
    lastActivity = Date.now();
    send({ type: 'autonomy', minutes: Math.round(mins * idleCount) });
  }
}, 10000);

// ---------- Настройки ----------
async function loadSettings() {
  const r = await (await fetch('/api/settings')).json();
  S = r.settings;
  META = r.meta;
  fillOptions($('presetSelect'), Object.keys(META.presets));
  fillOptions($('animePreset'), [...Object.keys(META.anime_presets), 'Свой']);
  fillOptions($('sileroSpeaker'), META.silero_speakers);
  fillOptions($('edgeVoice'), META.edge_voices);
  fillOptions($('sttModel'), META.stt_models);
  fillOptions($('vrmSelect'), META.vrm_files);
  fillOptions($('outfitSelect'), META.outfits || ['Обычная']);
  if (MODE !== 'mascot') {
    await refreshModels();
    renderRecommended();
  }
  syncInputs();
  applyLive();
  updateCloudBox();
  $('voiceModuleInfo').textContent = META.voice_module
    ? (META.voice_ref ? 'Голосовой модуль установлен, образец есть.' : 'Голосовой модуль установлен. Образец создастся из её голоса при первой песне.')
    : 'Голосовой модуль не установлен — запусти setup_voice.bat.';
  $('sttStatus').textContent = 'Статус: ' + r.status.stt + ' | Голос: ' + r.status.tts;
  return r.status;
}

function fillOptions(sel, items) {
  sel.innerHTML = '';
  for (const it of items) {
    const o = document.createElement('option');
    o.value = o.textContent = it;
    sel.appendChild(o);
  }
}

const RUDE = ['вежливая', 'подколы', 'хамка', 'без цензуры 🔞'];

async function updateCloudBox() {
  const p = S.llm_provider || 'ollama';
  $('cloudBox').classList.toggle('hidden', p === 'ollama');
  if (p === 'ollama' || !META.cloud) return;
  const c = META.cloud[p];
  $('cloudHint').innerHTML = `${c.title}. Ключ бесплатный: <a href="${c.key_url}" target="_blank">${c.key_url}</a>`;
  if (document.activeElement !== $('cloudKey')) $('cloudKey').value = S[p + '_key'] || '';
  const r = await (await fetch('/api/cloud/models?provider=' + p)).json();
  const list = r.models.includes(S[p + '_model']) ? r.models : [S[p + '_model'], ...r.models].filter(Boolean);
  fillOptions($('cloudModel'), list);
  $('cloudModel').value = S[p + '_model'];
}

function syncInputs() {
  document.querySelectorAll('[data-key]').forEach((el) => {
    if (document.activeElement === el) return;
    const v = S[el.dataset.key];
    if (el.type === 'checkbox') el.checked = !!v;
    else if (v !== undefined) el.value = v;
  });
  document.querySelectorAll('[data-show]').forEach((el) => { el.textContent = S[el.dataset.show]; });
  $('rudeLabel').textContent = RUDE[+S.rudeness] || '';
}

let prevVrm = null;
function applyLive() {
  $('nameLabel').textContent = S.assistant_name;
  document.title = `${S.assistant_name} — ИИ ассистент`;
  if (audio.gain) audio.gain.gain.value = +S.volume;
  mic?.configure(S);
  $('micBtn').className = S.listen_mode === 'vad' ? 'on' : '';
  if (!avatar) return;
  avatar.setBackground(S.background);
  avatar.setDistance(MODE === 'mascot' ? +S.mascot_distance : +S.camera_distance);
  if (avatar.outfit !== S.outfit) avatar.setOutfit(S.outfit);
  if (S.vrm_file !== prevVrm) {
    prevVrm = S.vrm_file;
    avatar.load(S.vrm_file).then(() => { avatar.setEmotion('happy', 3); avatar.gesture('wave'); })
      .catch((e) => addMsg('err', 'Не удалось загрузить модель: ' + e.message));
  }
  desktop?.setOnTop(!!S.mascot_on_top);
  desktop?.micState(S.listen_mode !== 'off');
}

async function save(patch) {
  Object.assign(S, patch);
  syncInputs();
  applyLive();
  await fetch('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch),
  });
}

document.querySelectorAll('[data-key]').forEach((el) => {
  el.addEventListener(el.type === 'range' ? 'input' : 'change', () => {
    let v = el.type === 'checkbox' ? el.checked : el.value;
    if (el.dataset.type === 'number') v = parseFloat(v);
    const patch = { [el.dataset.key]: v };
    if (el.id === 'presetSelect') {
      patch.persona = META.presets[v];
      document.querySelector('[data-key=persona]').value = patch.persona;
    }
    if (el.id === 'animePreset' && META.anime_presets[v]) {
      [patch.anime_pitch, patch.anime_formant, patch.anime_expressive] = META.anime_presets[v];
    }
    if (['anime_pitch', 'anime_formant', 'anime_expressive'].includes(el.dataset.key)) patch.anime_preset = 'Свой';
    save(patch).then(() => {
      if (el.id === 'providerSelect') updateCloudBox();
      if (el.id === 'modelSelect') checkCloud();
    });
  });
});

async function refreshModels() {
  const r = await (await fetch('/api/ollama/models')).json();
  const local = r.models.length ? r.models : [];
  const cloud = r.cloud || [];
  const sel = $('modelSelect');
  sel.innerHTML = '';
  const group = (label, items, mark) => {
    if (!items.length) return;
    const g = document.createElement('optgroup');
    g.label = label;
    for (const it of items) {
      const o = document.createElement('option');
      o.value = it;
      o.textContent = mark + it.replace(/[-:]cloud$/, '');
      g.appendChild(o);
    }
    sel.appendChild(g);
  };
  group('На этом ПК', local.includes(S.llm_model) || S.llm_model.endsWith('cloud') ? local : [S.llm_model, ...local], '💻 ');
  group('Облако Ollama — считается на серверах (нужен вход в Ollama)', cloud, '☁ ');
  sel.value = S.llm_model;
  if (!r.ok) $('pullProgress').textContent = 'Ollama не отвечает: запусти её (start.bat делает это сам).';
  checkCloud();
  return local;
}

async function checkCloud() {
  const j = await (await fetch('/api/ollama/cloud_check')).json();
  $('cloudStatus').textContent = !j.cloud ? '' : j.ok ? '☁ Облако работает — твоя видеокарта свободна для голоса и слуха.'
    : '⚠ ' + j.error;
}

async function renderRecommended() {
  const installed = (await (await fetch('/api/ollama/models')).json()).models;
  const box = $('recommended');
  box.innerHTML = '';
  for (const m of META.recommended_llm) {
    const row = document.createElement('div');
    row.className = 'rec-item';
    row.innerHTML = `<div><b>${m.name}</b><small>${m.note}</small></div>`;
    const b = document.createElement('button');
    const has = installed.includes(m.name);
    b.textContent = has ? 'Выбрать' : 'Скачать';
    b.onclick = async () => {
      if (!has) await pull(m.name);
      await save({ llm_model: m.name });
      await refreshModels();
      renderRecommended();
    };
    row.appendChild(b);
    box.appendChild(row);
  }
}

async function pull(name) {
  const out = $('pullProgress');
  out.textContent = `Скачиваю ${name}...`;
  const r = await fetch('/api/ollama/pull', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
  });
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const lines = buf.split('\n');
    buf = lines.pop();
    for (const l of lines) {
      try {
        const j = JSON.parse(l);
        if (j.error) out.textContent = 'Ошибка: ' + j.error;
        else if (j.total) out.textContent = `${name}: ${j.status} ${Math.round((j.completed || 0) / j.total * 100)}%`;
        else out.textContent = `${name}: ${j.status}`;
      } catch {}
    }
  }
}

// ---------- Кнопки ----------
$('settingsBtn').onclick = () => $('settings').classList.toggle('hidden');
$('closeSettings').onclick = () => $('settings').classList.add('hidden');
$('logBtn').onclick = () => $('log').classList.toggle('hidden');
document.querySelectorAll('#tabs button').forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll('#tabs button, #settings section').forEach((x) => x.classList.remove('active'));
    b.classList.add('active');
    document.querySelector(`[data-panel=${b.dataset.tab}]`).classList.add('active');
  };
});
$('sendBtn').onclick = () => { sendChat($('textInput').value); $('textInput').value = ''; };
$('textInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('sendBtn').click(); });
$('stopBtn').onclick = stopAll;
$('clearMemory').onclick = () => send({ type: 'clear' });
$('refreshModels').onclick = refreshModels;
$('pullCustom').onclick = async () => {
  const n = $('customModel').value.trim();
  if (!n) return;
  await pull(n);
  await refreshModels();
};
$('ttsTest').onclick = async () => {
  const r = await fetch('/api/tts', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: $('ttsTestText').value }),
  });
  const buf = await r.arrayBuffer();
  if (!buf.byteLength) return;
  // в окне-панели нет аватара — просто проигрываем
  if (!audio.ctx) audio.init();
  audio.doneFlag = true;
  audio.enqueue({ raw: buf, text: $('ttsTestText').value, emotion: 'happy' });
};
$('vrmUpload').onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append('file', f);
  const r = await (await fetch('/api/upload_vrm', { method: 'POST', body: fd })).json();
  if (r.error) return addMsg('err', r.error);
  await loadSettings();
};
$('confirmYes').onclick = () => answerConfirm(true);
$('confirmNo').onclick = () => answerConfirm(false);
function answerConfirm(ok) {
  send({ type: 'confirm', id: $('confirm').dataset.id, ok });
  $('confirm').classList.add('hidden');
}

$('cloudKey').addEventListener('change', async () => {
  await save({ [S.llm_provider + '_key']: $('cloudKey').value.trim() });
  updateCloudBox();
});
$('cloudModel').addEventListener('change', () => save({ [S.llm_provider + '_model']: $('cloudModel').value }));
$('cloudRefresh').onclick = updateCloudBox;
$('voiceUpload').onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append('file', f);
  const r = await fetch('/api/upload_voice', { method: 'POST', body: fd });
  addMsg(r.ok ? 'tool' : 'err', r.ok ? 'Образец голоса загружен' : 'Не удалось загрузить образец');
  loadSettings();
};
$('voiceRefPlay').onclick = async () => {
  const r = await fetch('/api/voice_ref');
  if (!r.ok) return addMsg('err', 'Образца пока нет');
  if (!audio.ctx) audio.init();
  audio.enqueue({ raw: await r.arrayBuffer(), text: 'Образец голоса' });
};
$('voiceRefReset').onclick = async () => { await fetch('/api/reset_voice', { method: 'POST' }); loadSettings(); };
$('singBtn').onclick = () => {
  const q = $('singQuery').value.trim();
  if (q) sendChat('Спой песню ' + q);
};

async function loadMemory() {
  const j = await (await fetch('/api/memory')).json();
  if (document.activeElement !== $('selfNotes')) $('selfNotes').value = j.self || '';
  const box = $('memList');
  box.innerHTML = '';
  for (const m of j.items) {
    const row = document.createElement('div');
    row.className = 'rec-item';
    const d = new Date(m.ts * 1000);
    row.innerHTML = `<div>${m.kind === 'self' ? '💭' : '👤'} <span></span><small>${d.toLocaleString('ru')}</small></div>`;
    row.querySelector('span').textContent = m.text;
    const b = document.createElement('button');
    b.textContent = '✕';
    b.onclick = async () => { await fetch('/api/memory/' + m.id, { method: 'DELETE' }); loadMemory(); };
    row.appendChild(b);
    box.appendChild(row);
  }
  if (!j.items.length) box.innerHTML = '<p class="muted">Пока пусто — поговори с ней.</p>';
}
const postMem = (body) => fetch('/api/memory', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});
$('saveSelf').onclick = () => postMem({ self: $('selfNotes').value });
$('reflectNow').onclick = async () => {
  $('reflectNow').textContent = '🧠 думает...';
  await fetch('/api/reflect', { method: 'POST' });
  $('reflectNow').textContent = '🧠 Обдумать сейчас';
  loadMemory();
};
$('memAddBtn').onclick = async () => {
  const t = $('memAdd').value.trim();
  if (!t) return;
  $('memAdd').value = '';
  await postMem({ add: t });
  loadMemory();
};
document.querySelector('[data-tab=memory]').addEventListener('click', loadMemory);

// режим «по кнопке»: держать 🎤 или клавишу `
const micBtn = $('micBtn');
micBtn.addEventListener('pointerdown', () => { if (S.listen_mode === 'ptt') { mic?.pttDown(); micBtn.classList.add('rec'); } });
micBtn.addEventListener('pointerup', () => { if (S.listen_mode === 'ptt') { mic?.pttUp(); micBtn.classList.remove('rec'); } });
micBtn.addEventListener('click', () => { if (S.listen_mode !== 'ptt') toggleMic(); });
const isTyping = () => ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName);
addEventListener('keydown', (e) => {
  if (e.code === 'Backquote' && !e.repeat && !isTyping()) { mic?.pttDown(); micBtn.classList.add('rec'); }
  if (e.code === 'Escape') stopAll();
});
addEventListener('keyup', (e) => {
  if (e.code === 'Backquote') { mic?.pttUp(); micBtn.classList.remove('rec'); }
});

// ---------- Фигурка на рабочем столе ----------
function setupMascot() {
  const canvas = $('scene');
  // клики проходят сквозь прозрачные места окна
  avatar.onHoverChange = (over) => {
    if (!$('confirm').classList.contains('hidden')) return;
    desktop?.setIgnore(!over);
    canvas.style.cursor = over ? 'grab' : 'default';
  };
  desktop?.setIgnore(true);

  // перетаскивание окна за девушку, короткий клик — «тык»
  let down = null;
  canvas.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || !avatar.overModel) return;
    down = { x: e.screenX, y: e.screenY, moved: false };
    desktop?.dragStart();
  });
  addEventListener('pointermove', (e) => {
    if (down && Math.hypot(e.screenX - down.x, e.screenY - down.y) > 5) down.moved = true;
  });
  addEventListener('pointerup', () => {
    if (!down) return;
    desktop?.dragEnd();
    if (!down.moved) poke();
    down = null;
  });
  canvas.addEventListener('contextmenu', (e) => { e.preventDefault(); desktop?.contextMenu(); });

  // колесо — масштаб фигурки
  canvas.addEventListener('wheel', (e) => {
    const d = Math.max(0.3, Math.min(2, +S.mascot_distance + Math.sign(e.deltaY) * 0.08));
    save({ mascot_distance: +d.toFixed(2) });
  }, { passive: true });

  desktop?.onMenu((cmd) => {
    if (cmd === 'toggle-mic') toggleMic();
    if (cmd === 'stop') stopAll();
  });
}

let lastPoke = 0;
function poke() {
  avatar.poke();
  avatar.emote(['exclaim', 'anger', 'question', 'heart'][Math.floor(Math.random() * 4)]);
  if (busy || audio.playing || Date.now() - lastPoke < 15000) return;
  lastPoke = Date.now();
  sendChat('(Системное сообщение, не цитируй его: пользователь только что ткнул тебя курсором мышки. ' +
    'Отреагируй коротко и по-своему, одной фразой.)', true);
}

// ---------- Старт ----------
async function wake() {
  $('overlay').classList.add('hidden');
  if (SPEAKER) {
    if (!audio.ctx) audio.init();
    mic = new Mic({
      onSpeech: handleSpeech,
      onStart: onVoiceStart,
      onLevel: (l) => { $('meterFill').style.height = `${Math.round(l * 100)}%`; },
    });
    mic.configure(S);
    try { await mic.start(); } catch (e) { addMsg('err', 'Нет доступа к микрофону: ' + e.message); }
  }
  connect();
  setState('idle');
}

(async () => {
  const st = await loadSettings();
  $('loadInfo').textContent = `Распознавание: ${st.stt} · Голос: ${st.tts}`;
  setState('sleep');
  if (MODE === 'mascot') {
    setupMascot();
    await wake(); // в приложении звук разрешён без клика
  } else if (MODE === 'panel') {
    await wake();
  }
})();

$('startBtn').onclick = wake;
