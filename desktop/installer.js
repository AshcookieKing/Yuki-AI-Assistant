// Подготовка Юки в одном окне: проверка Python/Ollama, выбор папки для моделей, установка библиотек,
// скачивание моделей и (по желанию) голосовых модулей — с прогрессом и журналом, без отдельных консолей.
const { ipcMain, dialog, shell } = require('electron');
const { spawn, spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const http = require('http');

const LINKS = {
  python: 'https://www.python.org/downloads/release/python-3119/',
  ollama: 'https://ollama.com/download',
};
const VRM_URL = 'https://pixiv.github.io/three-vrm/packages/three-vrm/examples/models/VRM1_Constraint_Twist_Sample.vrm';
const SEEDVC_URL = 'https://codeload.github.com/Plachtaa/seed-vc/zip/refs/heads/main';
const BRAIN_LIGHT = 'qwen3.5:9b';
const EMBED = 'embeddinggemma:300m';
// Основной мозг: Qwen3.6 35B-A3B (Apache 2.0), сжатая до ~3 бит (Unsloth Dynamic IQ3_XXS) — ~13 ГБ вместо 72.
// Работает офлайн; MoE — на каждое слово считаются только ~3 млрд параметров, поэтому быстро даже частично в ОЗУ.
const HF = 'https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF/resolve/main/';
const BRAIN_BIG = { name: 'yuki-qwen3.6', gguf: 'Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf', mmproj: 'mmproj-F16.gguf', gb: 14 };
const MODELFILE = (gguf, mmproj) => `FROM ./${gguf}\nFROM ./${mmproj}\nTEMPLATE {{ .Prompt }}\nRENDERER qwen3.5\nPARSER qwen3.5\n` +
  'PARAMETER top_p 0.95\nPARAMETER presence_penalty 1.5\nPARAMETER temperature 0.8\nPARAMETER top_k 20\n';
const os = require('os');

const ENV = { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1', PIP_DISABLE_PIP_VERSION_CHECK: '1' };
const ANSI = /\x1b\[[0-9;?]*[A-Za-z]/g;

function quiet(cmd, args) {
  try {
    const r = spawnSync(cmd, args, { windowsHide: true, encoding: 'utf-8', timeout: 15000, env: ENV });
    return r.status === 0 ? (r.stdout || '').trim() : null;
  } catch { return null; }
}

// Python 3.11/3.12 (на 3.13 нет части колёс): сначала через лаунчер py, потом python из PATH
function findPython() {
  for (const v of ['3.11', '3.12']) {
    const exe = quiet('py', [`-${v}`, '-c', 'import sys, venv; print(sys.executable)']);
    if (exe) return { exe, version: v };
  }
  const out = quiet('python', ['-c', 'import sys, venv; print(sys.executable); print("%d.%d" % sys.version_info[:2])']);
  if (out) {
    const [exe, version] = out.split(/\r?\n/);
    if (['3.11', '3.12'].includes(version)) return { exe, version };
  }
  return null;
}

function findOllama() {
  const p = path.join(process.env.LOCALAPPDATA || '', 'Programs', 'Ollama', 'ollama.exe');
  if (fs.existsSync(p)) return p;
  const w = quiet('where', ['ollama']);
  return w ? w.split(/\r?\n/)[0] : null;
}

function hasNvidia() { return quiet('nvidia-smi', ['-L']) !== null; }

function vramGB() {
  const out = quiet('nvidia-smi', ['--query-gpu=memory.total', '--format=csv,noheader,nounits']);
  return out ? Math.round(Number(out.split(/\r?\n/)[0]) / 1024) : 0;
}

// большой мозг — если есть видеокарта от 8 ГБ и ОЗУ от 24 ГБ (часть экспертов MoE живёт в ОЗУ)
function pickBrain() {
  const ram = Math.round(os.totalmem() / 1e9);
  return (vramGB() >= 8 && ram >= 24) ? 'big' : 'light';
}

// модель уже есть в Ollama? Смотрим в её папку на диске — так работает, даже если Ollama сейчас не запущена
function hasOllamaModel(name) {
  const base = process.env.OLLAMA_MODELS || path.join(os.homedir(), '.ollama', 'models');
  return ['manifests-v2/ollama.com/library', 'manifests/registry.ollama.ai/library']
    .some((d) => fs.existsSync(path.join(base, d, name)));
}

function getSetting(root, key) {
  try { return JSON.parse(fs.readFileSync(path.join(root, 'data', 'settings.json'), 'utf-8'))[key]; } catch { return undefined; }
}

function setSetting(root, key, value) {
  const f = path.join(root, 'data', 'settings.json');
  let j = {};
  try { j = JSON.parse(fs.readFileSync(f, 'utf-8')); } catch {}
  j[key] = value;
  fs.mkdirSync(path.dirname(f), { recursive: true });
  fs.writeFileSync(f, JSON.stringify(j, null, 2), 'utf-8');
}

function freeGB(dir) {
  let d = dir;
  while (d && !fs.existsSync(d)) d = path.dirname(d) === d ? null : path.dirname(d);
  try { const s = fs.statfsSync(d || dir); return Math.round((s.bavail * s.bsize) / 1e9 * 10) / 10; } catch { return null; }
}

function ping(url) {
  return new Promise((resolve) => {
    const req = http.get(url, (res) => { res.resume(); resolve(res.statusCode === 200); });
    req.on('error', () => resolve(false));
    req.setTimeout(1500, () => { req.destroy(); resolve(false); });
  });
}

function killTree(p) {
  if (p && !p.killed) spawn('taskkill', ['/pid', String(p.pid), '/T', '/F'], { windowsHide: true });
}

module.exports = function registerInstaller(ctx) {
  // ctx: { root(), setRoot(dir), sync(), mlog(msg), voiceReady(), ready() }
  let running = false;
  let current = null;   // текущий процесс — чтобы отменить при закрытии окна
  let cancelled = false;
  let send = () => {};

  // что появилось в новой версии, но ещё не стоит у уже подготовленной Юки
  const upgrades = () => {
    if (!ctx.ready()) return [];
    const out = [];
    if (pickBrain() === 'big' && !hasOllamaModel(BRAIN_BIG.name))
      out.push({ id: 'brain', title: `Новый мозг Qwen3.6 35B, сжатая (~${BRAIN_BIG.gb} ГБ)` });
    const tts = path.join(ctx.root(), '.venv-tts');
    if (hasNvidia() && fs.existsSync(path.join(tts, 'pyvenv.cfg')) && !fs.existsSync(path.join(tts, 'Lib', 'site-packages', 'f5_tts')))
      out.push({ id: 'espeech', title: 'Живой голос ESpeech (~2 ГБ)' });
    if (hasNvidia() && fs.existsSync(path.join(tts, 'pyvenv.cfg')) && !fs.existsSync(path.join(ctx.root(), '.venv-qtts', 'pyvenv.cfg')))
      out.push({ id: 'qtts', title: 'Голос Ono_Anna — игривая девочка (~8 ГБ)' });
    return out;
  };

  const info = () => {
    const py = findPython();
    return {
      dataDir: ctx.root(), free: freeGB(ctx.root()),
      python: py ? `${py.version}` : null, ollama: !!findOllama(), nvidia: hasNvidia(),
      installed: ctx.ready(), voiceInstalled: ctx.voiceReady(), running, problem: ctx.problem(),
      brain: pickBrain(), vram: vramGB(), ram: Math.round(os.totalmem() / 1e9), upgrades: upgrades(),
    };
  };

  // запуск команды с разбором вывода построчно (pip и ollama рисуют прогресс через \r)
  function run(cmd, args, onLine) {
    return new Promise((resolve, reject) => {
      if (cancelled) return reject(new Error('Отменено'));
      ctx.mlog(`установка: ${cmd} ${args.join(' ')}`);
      const p = spawn(cmd, args, { cwd: ctx.root(), windowsHide: true, env: ENV });
      current = p;
      const tail = [];
      const feed = (buf) => {
        for (let line of buf.toString('utf-8').replace(ANSI, '').split(/[\r\n]+/)) {
          line = line.trim();
          if (!line) continue;
          tail.push(line); if (tail.length > 30) tail.shift();
          send({ type: 'log', line });
          onLine?.(line);
        }
      };
      p.stdout.on('data', feed);
      p.stderr.on('data', feed);
      p.on('error', (e) => { current = null; reject(e); });
      p.on('exit', (code) => {
        current = null;
        if (cancelled) return reject(new Error('Отменено'));
        if (code === 0) resolve();
        else reject(new Error(`«${path.basename(cmd)} ${args.slice(0, 4).join(' ')}» завершилась с кодом ${code}\n` + tail.slice(-8).join('\n')));
      });
    });
  }

  // pip: показываем, какой пакет качается/ставится
  const pipLine = (detail) => (line) => {
    let m = line.match(/^Downloading (\S+?)(?:-\d[^\s]*)?(?:\.whl|\.tar\.gz)?\s*\(([^)]+)\)/);
    if (m) return detail(`Скачиваю ${path.basename(m[1])} (${m[2]})`);
    m = line.match(/^Collecting (\S+)/);
    if (m) return detail(`Ищу ${m[1]}`);
    if (line.startsWith('Installing collected packages')) detail('Устанавливаю пакеты…');
  };

  async function download(url, dest, detail) {
    if (fs.existsSync(dest)) return;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Не удалось скачать ${url}: HTTP ${res.status}`);
    const total = Number(res.headers.get('content-length')) || 0;
    const out = fs.createWriteStream(dest + '.part');
    let got = 0;
    for await (const chunk of res.body) {
      if (cancelled) { out.destroy(); throw new Error('Отменено'); }
      out.write(chunk);
      got += chunk.length;
      detail(`${(got / 1e6).toFixed(1)} МБ` + (total ? ` из ${(total / 1e6).toFixed(1)} МБ` : ''), total ? got / total : null);
    }
    await new Promise((r) => out.end(r));
    fs.renameSync(dest + '.part', dest);
  }

  async function ensureOllama(exe) {
    if (await ping('http://127.0.0.1:11434/api/version')) return;
    spawn(exe, ['serve'], { detached: true, stdio: 'ignore', windowsHide: true }).unref();
    for (let i = 0; i < 30; i++) {
      if (await ping('http://127.0.0.1:11434/api/version')) return;
      await new Promise((r) => setTimeout(r, 500));
    }
    throw new Error('Ollama не запустилась. Открой приложение Ollama и нажми «Повторить».');
  }

  // ollama pull: «pulling 8eeb…:  45% ▕██▏ 2.9 GB/6.6 GB  52 MB/s»
  const pullLine = (detail) => (line) => {
    const m = line.match(/(\d+)%.*?([\d.]+\s*[KMG]?B)\s*\/\s*([\d.]+\s*[KMG]?B)/);
    if (m) detail(`${m[2]} из ${m[3]}`, Number(m[1]) / 100);
    else if (/verifying|writing manifest|success/i.test(line)) detail('Проверяю…');
  };

  function steps(voice, brainChoice, upgrade) {
    const R = () => ctx.root();
    const vpy = (venv) => path.join(R(), venv, 'Scripts', 'python.exe');
    const py = findPython();
    // создаёт окружение; недоделанное (нет pyvenv.cfg или python.exe) удаляет и создаёт заново
    const ensureVenv = async (name) => {
      const dir = path.join(R(), name);
      if (fs.existsSync(vpy(name)) && fs.existsSync(path.join(dir, 'pyvenv.cfg'))) return;
      if (fs.existsSync(dir)) { ctx.mlog(`окружение ${name} повреждено — пересоздаю`); fs.rmSync(dir, { recursive: true, force: true }); }
      await run(py.exe, ['-m', 'venv', name]);
    };
    const ollama = findOllama();
    const list = [
      { id: 'files', title: 'Файлы Юки', w: 1, run: async () => ctx.sync() },
      { id: 'venv', title: 'Окружение Python', w: 3, run: async () => {
        await ensureVenv('.venv');
        await run(vpy('.venv'), ['-m', 'pip', 'install', '--upgrade', 'pip']);
      } },
      { id: 'torch', title: 'Нейросетевой движок PyTorch', w: 14, run: async (d) => {
        await run(vpy('.venv'), ['-m', 'pip', 'install', 'torch', '--index-url', 'https://download.pytorch.org/whl/cpu'], pipLine(d));
      } },
      { id: 'libs', title: 'Слух, голос и управление ПК', w: 14, run: async (d) => {
        await run(vpy('.venv'), ['-m', 'pip', 'install', '-r', 'requirements.txt'], pipLine(d));
      } },
      { id: 'vrm', title: '3D-модель', w: 2, run: async (d) => {
        const dest = path.join(R(), 'static', 'models', 'default.vrm');
        fs.mkdirSync(path.dirname(dest), { recursive: true });
        if (!fs.existsSync(dest)) await download(VRM_URL, dest, d);
      } },
      ...(brainChoice === 'big' ? [
        { id: 'brain', title: `Мозг — Qwen3.6 35B, сжатая (~${BRAIN_BIG.gb} ГБ)`, w: 40, run: async (d) => {
          await ensureOllama(ollama);
          const dir = path.join(R(), 'models');
          fs.mkdirSync(dir, { recursive: true });
          await download(HF + BRAIN_BIG.gguf, path.join(dir, BRAIN_BIG.gguf), d);
          await download(HF + BRAIN_BIG.mmproj, path.join(dir, BRAIN_BIG.mmproj), d);
          fs.writeFileSync(path.join(dir, 'Modelfile.yuki'), MODELFILE(BRAIN_BIG.gguf, BRAIN_BIG.mmproj));
          d('Подключаю модель к Ollama…');
          await new Promise((res, rej) => {
            const p = spawn(ollama, ['create', BRAIN_BIG.name, '-f', 'Modelfile.yuki'], { cwd: dir, windowsHide: true, env: ENV });
            current = p;
            p.stdout.on('data', (b) => send({ type: 'log', line: b.toString().trim() }));
            p.stderr.on('data', (b) => send({ type: 'log', line: b.toString().trim() }));
            p.on('exit', (c) => { current = null; c === 0 ? res() : rej(new Error('ollama create завершилась с кодом ' + c)); });
          });
          // Ollama скопировала модель к себе — исходники больше не нужны
          for (const f of [BRAIN_BIG.gguf, BRAIN_BIG.mmproj]) fs.rmSync(path.join(dir, f), { force: true });
          // свой выбор (облако, другая модель) не трогаем — меняем только стандартный мозг
          const cur = getSetting(R(), 'llm_model');
          if (!upgrade || !cur || ['qwen3.5:9b', 'qwen3:8b', 'qwen3:4b', 'qwen3:14b'].includes(cur)) {
            setSetting(R(), 'llm_model', BRAIN_BIG.name);
            setSetting(R(), 'num_ctx', 8192);
          }
        } },
      ] : [
        { id: 'brain', title: `Мозг — ${BRAIN_LIGHT} (~6.6 ГБ)`, w: 34, run: async (d) => {
          await ensureOllama(ollama);
          await run(ollama, ['pull', BRAIN_LIGHT], pullLine(d));
          setSetting(R(), 'llm_model', BRAIN_LIGHT);
        } },
      ]),
      { id: 'memory', title: 'Память — embeddinggemma', w: 4, run: async (d) => {
        await run(ollama, ['pull', EMBED], pullLine(d));
        fs.mkdirSync(path.join(R(), 'data'), { recursive: true });
        fs.writeFileSync(path.join(R(), 'data', '.installed'), 'ok');
      } },
    ];
    if (upgrade) {
      // доставка нового поверх уже подготовленной Юки: только недостающие шаги
      const need = new Set(upgrades().map((u) => u.id));
      const pick = (id) => list.find((x) => x.id === id);
      const out = [];
      if (need.has('brain')) out.push(pick('brain'));
      if (need.has('espeech')) {
        out.push({ id: 'espeech', title: 'Живой голос ESpeech', w: 10, run: async (d) => {
          const T = vpy('.venv-tts');
          const cons = path.join(R(), 'data', 'tts_constraints.txt');
          fs.writeFileSync(cons, 'torch==2.6.0+cu124\ntorchaudio==2.6.0+cu124\nnumpy==1.26.4\n');
          await run(T, ['-m', 'pip', 'install', '-c', cons, '--extra-index-url', 'https://download.pytorch.org/whl/cu124',
            'f5-tts', 'ruaccent', 'pyarrow<17', 'datasets<4'], pipLine(d));
          d('Скачиваю голосовую модель ESpeech…');
          ENV.HF_HOME = path.join(R(), 'data', 'hf');
          await run(T, ['-c', "from huggingface_hub import hf_hub_download as h; [h('ESpeech/ESpeech-TTS-1_RL-V2', f) for f in ('espeech_tts_rlv2.pt','vocab.txt')]"]);
          setSetting(R(), 'tts_engine', 'espeech');
        } });
      }
      if (need.has('qtts')) out.push(steps(true, brainChoice, false).find((x) => x.id === 'qtts'));
      return out;
    }
    if (voice) {
      list.push(
        { id: 'tts', title: 'Живой нейро-голос ESpeech + Chatterbox (CUDA)', w: 16, run: async (d) => {
          await ensureVenv('.venv-tts');
          const T = vpy('.venv-tts');
          const cons = path.join(R(), 'data', 'tts_constraints.txt');
          fs.mkdirSync(path.dirname(cons), { recursive: true });
          fs.writeFileSync(cons, 'torch==2.6.0+cu124\ntorchaudio==2.6.0+cu124\nnumpy==1.26.4\n');
          await run(T, ['-m', 'pip', 'install', '--upgrade', 'pip']);
          await run(T, ['-m', 'pip', 'install', 'torch==2.6.0+cu124', 'torchaudio==2.6.0+cu124',
            '--index-url', 'https://download.pytorch.org/whl/cu124'], pipLine(d));
          await run(T, ['-m', 'pip', 'install', '-c', cons, '--extra-index-url', 'https://download.pytorch.org/whl/cu124',
            'chatterbox-tts', 'f5-tts', 'ruaccent', 'pyarrow<17', 'datasets<4'], pipLine(d));
          d('Скачиваю голосовую модель ESpeech…');
          ENV.HF_HOME = path.join(R(), 'data', 'hf');  // модели голоса — в папку данных, не на системный диск
          await run(T, ['-c', "from huggingface_hub import hf_hub_download as h; [h('ESpeech/ESpeech-TTS-1_RL-V2', f) for f in ('espeech_tts_rlv2.pt','vocab.txt')]"]);
          setSetting(R(), 'tts_engine', 'espeech');
        } },
        { id: 'qtts', title: 'Голос Qwen3-TTS — Ono_Anna, игривая девочка (CUDA)', w: 12, run: async (d) => {
          await ensureVenv('.venv-qtts');
          const Q = vpy('.venv-qtts');
          await run(Q, ['-m', 'pip', 'install', '--upgrade', 'pip']);
          await run(Q, ['-m', 'pip', 'install', 'torch==2.6.0+cu124', 'torchaudio==2.6.0+cu124',
            '--index-url', 'https://download.pytorch.org/whl/cu124'], pipLine(d));
          // faster-qwen3-tts (CUDA-графы, в ~5 раз быстрее) + проверенная версия transformers
          const qc = path.join(R(), 'data', 'qtts_constraints.txt');
          fs.mkdirSync(path.dirname(qc), { recursive: true });
          fs.writeFileSync(qc, ['torch==2.6.0+cu124', 'torchaudio==2.6.0+cu124', 'transformers==5.15.1', ''].join('\n'));
          await run(Q, ['-m', 'pip', 'install', '-c', qc, '--extra-index-url', 'https://download.pytorch.org/whl/cu124',
            'faster-qwen3-tts', 'transformers==5.15.1'], pipLine(d));
          d('Скачиваю голосовую модель Qwen3-TTS…');
          ENV.HF_HOME = path.join(R(), 'data', 'hf');
          await run(Q, ['-c', "from huggingface_hub import snapshot_download as s; s('Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice')"]);
          setSetting(R(), 'tts_engine', 'qwen3tts');
        } },
        { id: 'seedvc', title: 'Перенос голоса и пение — Seed-VC', w: 3, run: async (d) => {
          const singer = path.join(R(), 'singer');
          if (fs.existsSync(path.join(singer, 'seed-vc'))) return;
          const zip = path.join(singer, 'seed-vc.zip');
          await download(SEEDVC_URL, zip, d);
          d('Распаковываю…');
          await run('tar', ['-xf', zip, '-C', singer]);
          fs.renameSync(path.join(singer, 'seed-vc-main'), path.join(singer, 'seed-vc'));
          fs.rmSync(zip, { force: true });
        } },
        { id: 'sing', title: 'Пение: библиотеки (CUDA)', w: 14, run: async (d) => {
          await ensureVenv('.venv-sing');
          const P = vpy('.venv-sing');
          await run(P, ['-m', 'pip', 'install', '--upgrade', 'pip']);
          await run(P, ['-m', 'pip', 'install', 'torch==2.4.0', 'torchaudio==2.4.0', '--index-url', 'https://download.pytorch.org/whl/cu121'], pipLine(d));
          await run(P, ['-m', 'pip', 'install', 'numpy==1.26.4', 'scipy==1.13.1', 'librosa==0.10.2', 'huggingface-hub>=0.28.1',
            'munch==4.0.0', 'einops==0.8.0', 'descript-audio-codec==1.0.0', 'transformers==4.46.3', 'soundfile',
            'hydra-core==1.3.2', 'pyyaml', 'pydub', 'accelerate', 'demucs', 'yt-dlp', 'imageio-ffmpeg'], pipLine(d));
          fs.writeFileSync(path.join(R(), 'data', '.voice_installed'), 'ok');
        } },
      );
    }
    return list;
  }

  ipcMain.handle('setup-info', () => info());

  ipcMain.handle('setup-pick-dir', async (e) => {
    if (running) return info();
    const r = await dialog.showOpenDialog({
      title: 'Куда поставить модели и данные Юки (~15 ГБ)', defaultPath: path.dirname(ctx.root()),
      properties: ['openDirectory', 'createDirectory'],
    });
    if (!r.canceled && r.filePaths[0]) {
      let dir = r.filePaths[0];
      // выбрали диск или чужую папку — кладём всё в отдельную подпапку
      if (!/yuki/i.test(path.basename(dir))) dir = path.join(dir, 'YukiData');
      ctx.setRoot(dir);
    }
    return info();
  });

  ipcMain.on('setup-open', (_e, what) => { if (LINKS[what]) shell.openExternal(LINKS[what]); });

  ipcMain.on('setup-start', async (e, opts) => {
    if (running) return;
    const wc = e.sender;
    send = (m) => { if (!wc.isDestroyed()) wc.send('setup-event', m); };
    const i = info();
    if (!i.python || !i.ollama) return send({ type: 'error', message: 'Сначала установи недостающие программы.' });
    running = true;
    cancelled = false;
    const list = steps(!!opts?.voice, opts?.brain || i.brain, !!opts?.upgrade);
    const total = list.reduce((s, x) => s + x.w, 0);
    let doneW = 0;
    send({ type: 'plan', steps: list.map(({ id, title }) => ({ id, title })) });
    try {
      for (const st of list) {
        send({ type: 'step', id: st.id, state: 'run' });
        const detail = (text, frac = null) => send({
          type: 'progress', id: st.id, detail: text, frac,
          overall: (doneW + st.w * (frac ?? 0)) / total,
        });
        detail('');
        await st.run(detail);
        doneW += st.w;
        send({ type: 'step', id: st.id, state: 'done' });
        send({ type: 'progress', id: st.id, detail: '', frac: 1, overall: doneW / total });
      }
      ctx.mlog('подготовка завершена');
      send({ type: 'done' });
    } catch (err) {
      ctx.mlog('подготовка: ' + (err?.stack || err));
      send({ type: 'error', message: String(err?.message || err) });
    } finally {
      running = false;
    }
  });

  return {
    isRunning: () => running,
    upgrades,
    cancel: () => { cancelled = true; killTree(current); },
  };
};
