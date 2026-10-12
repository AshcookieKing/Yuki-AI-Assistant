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
const BRAIN = 'qwen3.5:9b';
const EMBED = 'embeddinggemma:300m';

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

  const info = () => {
    const py = findPython();
    return {
      dataDir: ctx.root(), free: freeGB(ctx.root()),
      python: py ? `${py.version}` : null, ollama: !!findOllama(), nvidia: hasNvidia(),
      installed: ctx.ready(), voiceInstalled: ctx.voiceReady(), running, problem: ctx.problem(),
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

  function steps(voice) {
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
      { id: 'brain', title: `Мозг — ${BRAIN} (~6.6 ГБ)`, w: 34, run: async (d) => {
        await ensureOllama(ollama);
        await run(ollama, ['pull', BRAIN], pullLine(d));
      } },
      { id: 'memory', title: 'Память — embeddinggemma', w: 4, run: async (d) => {
        await run(ollama, ['pull', EMBED], pullLine(d));
        fs.mkdirSync(path.join(R(), 'data'), { recursive: true });
        fs.writeFileSync(path.join(R(), 'data', '.installed'), 'ok');
      } },
    ];
    if (voice) {
      list.push(
        { id: 'tts', title: 'Нейро-голос Chatterbox (CUDA)', w: 14, run: async (d) => {
          await ensureVenv('.venv-tts');
          await run(vpy('.venv-tts'), ['-m', 'pip', 'install', '--upgrade', 'pip']);
          await run(vpy('.venv-tts'), ['-m', 'pip', 'install', 'chatterbox-tts'], pipLine(d));
          // chatterbox тянет CPU-версию torch — меняем на CUDA без зависимостей
          await run(vpy('.venv-tts'), ['-m', 'pip', 'install', 'torch==2.6.0+cu124', 'torchaudio==2.6.0+cu124',
            '--force-reinstall', '--no-deps', '--index-url', 'https://download.pytorch.org/whl/cu124'], pipLine(d));
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
    const list = steps(!!opts?.voice);
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
    cancel: () => { cancelled = true; killTree(current); },
  };
};
