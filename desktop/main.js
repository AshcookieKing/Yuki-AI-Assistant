// Фигурка ассистента на рабочем столе: прозрачное окно без рамки поверх всех окон,
// плюс отдельное окно настроек и чата. Сервер (Python) и Ollama запускаются отсюда.
const { app, BrowserWindow, ipcMain, Menu, Tray, nativeImage, screen, session } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const http = require('http');

// В установленной версии код сервера лежит в resources/backend, а всё рабочее (Python, модели, память)
// — в отдельной папке данных (не внутри папки программы: деинсталлятор и обновления её стирают).
// Папку выбирает пользователь в окне подготовки, путь хранится в %APPDATA%\Yuki\config.json.
const PACKAGED = app.isPackaged;
const CONFIG_FILE = path.join(app.getPath('userData'), 'config.json');
const LEGACY_ROOT = path.join(process.env.LOCALAPPDATA || app.getPath('userData'), 'Yuki', 'backend');

function defaultDataDir() {
  if (fs.existsSync(path.join(LEGACY_ROOT, 'data', '.installed'))) return LEGACY_ROOT;
  // программа стоит не в профиле (например D:\Yuki) — данные рядом, на том же диске: D:\YukiData
  const installDir = path.dirname(process.execPath);
  const local = (process.env.LOCALAPPDATA || '').toLowerCase();
  if (local && !installDir.toLowerCase().startsWith(local)) return path.join(path.dirname(installDir), 'YukiData');
  return LEGACY_ROOT;
}

function loadDataDir() {
  try { return JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf-8')).dataDir || defaultDataDir(); } catch { return defaultDataDir(); }
}

let ROOT = PACKAGED ? loadDataDir() : path.resolve(__dirname, '..');
const URL = 'http://127.0.0.1:8765';
const stateFile = () => path.join(ROOT, 'data', 'window.json');

function setDataDir(dir) {
  ROOT = dir;
  fs.mkdirSync(path.dirname(CONFIG_FILE), { recursive: true });
  fs.writeFileSync(CONFIG_FILE, JSON.stringify({ dataDir: dir }, null, 2));
  mlog('папка данных: ' + dir);
}

function syncBackend() {
  if (!PACKAGED) return;
  const src = path.join(process.resourcesPath, 'backend');
  const copy = (from, to) => {
    fs.mkdirSync(to, { recursive: true });
    for (const e of fs.readdirSync(from, { withFileTypes: true })) {
      const a = path.join(from, e.name);
      const b = path.join(to, e.name);
      if (e.isDirectory()) copy(a, b); else fs.copyFileSync(a, b);
    }
  };
  copy(src, ROOT);
  fs.mkdirSync(path.join(ROOT, 'data'), { recursive: true });
}

// окружение целое: есть и python.exe, и pyvenv.cfg (недоудалённая папка .venv без него не запускается)
const venvOk = (name) => fs.existsSync(path.join(ROOT, name, 'Scripts', 'python.exe'))
  && fs.existsSync(path.join(ROOT, name, 'pyvenv.cfg'));

function backendReady() {
  return venvOk('.venv') && (!PACKAGED || fs.existsSync(path.join(ROOT, 'data', '.installed')));
}

const voiceReady = () => venvOk('.venv-sing') && venvOk('.venv-tts');

// что пошло не так при запуске — показываем в окне подготовки с кнопкой «Починить»
let startProblem = '';

// окно подготовки — один стилизованный загрузчик, вся установка идёт внутри него (installer.js)
const installer = require('./installer')({
  root: () => ROOT, setRoot: setDataDir, sync: syncBackend, mlog, ready: backendReady, voiceReady,
  problem: () => startProblem,
});

let setupWin = null;
function openSetup() {
  if (setupWin) {
    if (startProblem && !installer.isRunning()) setupWin.reload();  // показать экран с «Починить»
    setupWin.show(); setupWin.focus(); return;
  }
  setupWin = new BrowserWindow({
    width: 860, height: 640, minWidth: 720, minHeight: 560, title: 'Юки — подготовка', frame: false,
    backgroundColor: '#140f26', icon: path.join(__dirname, 'icon.ico'),
    webPreferences: { preload: path.join(__dirname, 'setup-preload.js') },
  });
  setupWin.loadFile(path.join(__dirname, 'setup.html'));
  setupWin.on('close', (e) => {
    if (!installer.isRunning()) return;
    const { dialog } = require('electron');
    const r = dialog.showMessageBoxSync(setupWin, {
      type: 'warning', buttons: ['Продолжить установку', 'Прервать'], defaultId: 0, cancelId: 0,
      title: 'Юки', message: 'Подготовка ещё идёт. Прервать?', detail: 'Потом можно будет продолжить с того же места.',
    });
    if (r === 0) e.preventDefault(); else installer.cancel();
  });
  setupWin.on('closed', () => { mlog('окно подготовки закрыто'); setupWin = null; if (!mascot) app.quit(); });
}

ipcMain.on('setup-window', (_e, cmd) => {
  if (cmd === 'min') setupWin?.minimize();
  if (cmd === 'close') setupWin?.close();
});
ipcMain.on('setup-launch', async () => {
  if (!backendReady()) return;
  if (mascot) { setupWin?.close(); return; }
  setupWin?.hide();
  await startMascot();
  if (mascot) setupWin?.destroy();  // не запустилась — окно уже снова показано с «Починить»
});

// журнал запуска — помогает понять, почему программа не стартовала
const LOG_FILE = path.join(app.getPath('temp'), 'yuki-main.log');
function mlog(msg) { try { fs.appendFileSync(LOG_FILE, `[${new Date().toISOString()}] ${msg}
`); } catch {} }
process.on('uncaughtException', (e) => mlog('ОШИБКА: ' + (e.stack || e)));
mlog(`старт, packaged=${app.isPackaged}, ROOT=${ROOT}`);

app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');
if (!app.requestSingleInstanceLock()) { mlog('уже запущена другая копия — выхожу'); app.quit(); }

let mascot = null;
let panel = null;
let tray = null;
let server = null;
let onTop = true;
let micOn = true;

function ping(url) {
  return new Promise((resolve) => {
    const req = http.get(url, (res) => { res.resume(); resolve(res.statusCode === 200); });
    req.on('error', () => resolve(false));
    req.setTimeout(1500, () => { req.destroy(); resolve(false); });
  });
}

async function ensureBackend() {
  if (!(await ping('http://127.0.0.1:11434/api/version'))) {
    const ollama = path.join(process.env.LOCALAPPDATA || '', 'Programs', 'Ollama', 'ollama.exe');
    if (fs.existsSync(ollama)) spawn(ollama, ['serve'], { detached: true, stdio: 'ignore', windowsHide: true }).unref();
  }
  if (!(await ping(URL + '/api/status'))) {
    const py = path.join(ROOT, '.venv', 'Scripts', 'python.exe');
    fs.mkdirSync(path.join(ROOT, 'data'), { recursive: true });
    const log = fs.openSync(path.join(ROOT, 'data', 'server.log'), 'a');
    server = spawn(py, ['server.py'], { cwd: ROOT, windowsHide: true, stdio: ['ignore', log, log],
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
  }
  for (let i = 0; i < 120; i++) {
    if (await ping(URL + '/api/status')) return true;
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

function loadState() {
  try { return JSON.parse(fs.readFileSync(stateFile(), 'utf-8')); } catch { return {}; }
}
function saveState() {
  if (!mascot) return;
  const [x, y] = mascot.getPosition();
  const [width, height] = mascot.getSize();
  fs.writeFileSync(stateFile(), JSON.stringify({ x, y, width, height }));
}

function defaultBounds() {
  const wa = screen.getPrimaryDisplay().workArea;
  const width = 380, height = 640;
  return { x: wa.x + wa.width - width - 20, y: wa.y + wa.height - height, width, height };
}

function createMascot() {
  const st = { ...defaultBounds(), ...loadState() };
  // если сохранённая позиция за пределами экранов — возвращаем в угол
  const visible = screen.getAllDisplays().some((d) => {
    const a = d.workArea;
    return st.x + 50 > a.x && st.x < a.x + a.width - 50 && st.y + 50 > a.y && st.y < a.y + a.height - 50;
  });
  const b = visible ? st : defaultBounds();
  mascot = new BrowserWindow({
    ...b,
    transparent: true, frame: false, resizable: false, hasShadow: false, skipTaskbar: true,
    alwaysOnTop: onTop, backgroundColor: '#00000000', title: 'Юки',
    webPreferences: { preload: path.join(__dirname, 'preload.js'), backgroundThrottling: false },
  });
  mascot.setAlwaysOnTop(onTop, 'floating');
  mascot.setIgnoreMouseEvents(true, { forward: true });
  mascot.loadURL(URL + '/?mode=mascot');
  mascot.on('moved', saveState);
  mascot.on('closed', () => { mascot = null; });
}

function openPanel() {
  if (panel) { panel.show(); panel.focus(); return; }
  panel = new BrowserWindow({
    width: 1100, height: 780, minWidth: 700, minHeight: 500, title: 'Юки — настройки и чат',
    backgroundColor: '#1b1630', autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, 'preload.js') },
  });
  panel.loadURL(URL + '/?mode=panel');
  panel.on('closed', () => { panel = null; });
}

function menuTemplate() {
  return [
    { label: 'Настройки и чат', click: openPanel },
    { label: micOn ? 'Выключить микрофон' : 'Включить микрофон',
      click: () => mascot?.webContents.send('menu', 'toggle-mic') },
    { label: 'Замолчи', click: () => mascot?.webContents.send('menu', 'stop') },
    { type: 'separator' },
    { label: 'Поверх всех окон', type: 'checkbox', checked: onTop, click: (i) => setOnTop(i.checked, true) },
    { label: 'Вернуть в угол экрана', click: () => { mascot?.setBounds(defaultBounds()); saveState(); } },
    { label: 'Перезагрузить', click: () => mascot?.reload() },
    ...(PACKAGED ? [{ label: voiceReady() ? 'Подготовка и модули…' : 'Установить пение и нейро-голос…', click: openSetup }] : []),
    { type: 'separator' },
    updateState === 'готово'
      ? { label: 'Установить обновление и перезапустить', click: () => updater?.quitAndInstall(true, true) }
      : { label: updateState ? `Обновление: ${updateState}` : `Проверить обновления (v${app.getVersion()})`,
          enabled: !updateState, click: () => checkUpdates(true) },
    { type: 'separator' },
    { label: 'Выход', click: () => app.quit() },
  ];
}

// ---------- автообновление с GitHub Releases ----------
// при запуске и раз в 4 часа смотрим latest.yml последнего релиза; новая версия качается в фоне,
// потом спрашиваем, перезапустить ли сейчас (иначе поставится при выходе)
let updater = null;
let updateState = '';  // '', 'проверка', 'скачивается N%', 'готово'
function setUpdateState(s) {
  updateState = s;
  tray?.setContextMenu(Menu.buildFromTemplate(menuTemplate()));
}

function initUpdater() {
  if (!PACKAGED) return;
  try { ({ autoUpdater: updater } = require('electron-updater')); } catch (e) { mlog('updater: ' + e); return; }
  updater.autoDownload = true;
  updater.autoInstallOnAppQuit = true;
  updater.logger = { info: (m) => mlog('upd: ' + m), warn: (m) => mlog('upd! ' + m), error: (m) => mlog('upd ERR ' + m), debug: () => {} };
  updater.on('checking-for-update', () => setUpdateState('проверка'));
  updater.on('update-not-available', () => setUpdateState(''));
  updater.on('update-available', (i) => { mlog('найдена версия ' + i.version); setUpdateState('скачивается'); });
  updater.on('download-progress', (p) => setUpdateState(`скачивается ${Math.round(p.percent)}%`));
  updater.on('error', (e) => { mlog('ошибка обновления: ' + (e?.message || e)); setUpdateState(''); });
  updater.on('update-downloaded', async (i) => {
    setUpdateState('готово');
    const { dialog } = require('electron');
    const r = await dialog.showMessageBox({
      type: 'info', title: 'Юки — обновление', buttons: ['Перезапустить сейчас', 'Позже'], defaultId: 0, cancelId: 1,
      message: `Скачана новая версия Юки ${i.version}.`,
      detail: 'Перезапустить и установить сейчас? Если выбрать «Позже», обновление поставится при выходе.',
    });
    if (r.response === 0) updater.quitAndInstall(true, true);
  });
  checkUpdates();
  setInterval(checkUpdates, 4 * 60 * 60 * 1000);
}

function checkUpdates(manual = false) {
  if (!updater) {
    if (manual) require('electron').dialog.showMessageBox({ title: 'Юки', message: 'Обновления работают только в установленной версии.' });
    return;
  }
  updater.checkForUpdates().then((r) => {
    if (manual && (!r || !r.isUpdateAvailable)) {
      require('electron').dialog.showMessageBox({ title: 'Юки', message: `У тебя последняя версия (${app.getVersion()}).` });
    }
  }).catch((e) => {
    mlog('проверка обновлений: ' + (e?.message || e));
    if (manual) require('electron').dialog.showErrorBox('Юки', 'Не удалось проверить обновления: ' + (e?.message || e));
  });
}

function setOnTop(v, persist) {
  onTop = v;
  mascot?.setAlwaysOnTop(v, 'floating');
  if (persist) {
    const req = http.request(URL + '/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
    req.on('error', () => {});
    req.end(JSON.stringify({ mascot_on_top: v }));
  }
}

function trayIcon() {
  const p = path.join(__dirname, 'icon.png');  // иконка трея
  return fs.existsSync(p) ? nativeImage.createFromPath(p) : nativeImage.createEmpty();
}

// ---------- IPC от окна фигурки ----------
ipcMain.on('set-ignore', (e, ignore) => {
  const w = BrowserWindow.fromWebContents(e.sender);
  if (w === mascot) mascot.setIgnoreMouseEvents(ignore, { forward: true });
});

let dragTimer = null;
ipcMain.on('drag-start', () => {
  if (!mascot) return;
  const cur = screen.getCursorScreenPoint();
  const [wx, wy] = mascot.getPosition();
  const off = { x: cur.x - wx, y: cur.y - wy };
  const [w, h] = mascot.getSize();
  clearInterval(dragTimer);
  dragTimer = setInterval(() => {
    const c = screen.getCursorScreenPoint();
    mascot?.setBounds({ x: c.x - off.x, y: c.y - off.y, width: w, height: h });
  }, 10);
});
ipcMain.on('drag-end', () => { clearInterval(dragTimer); dragTimer = null; saveState(); });
ipcMain.on('context-menu', () => Menu.buildFromTemplate(menuTemplate()).popup({ window: mascot }));
ipcMain.on('open-panel', openPanel);
ipcMain.on('set-on-top', (_e, v) => setOnTop(!!v, false));
ipcMain.on('mic-state', (_e, v) => { micOn = !!v; tray?.setContextMenu(Menu.buildFromTemplate(menuTemplate())); });

app.whenReady().then(async () => {
  // микрофон разрешён нашим локальным страницам
  session.defaultSession.setPermissionRequestHandler((wc, perm, cb) => cb(perm === 'media'));
  session.defaultSession.setPermissionCheckHandler((wc, perm) => perm === 'media');

  tray = new Tray(trayIcon());
  tray.setToolTip('Юки — ИИ ассистент');
  tray.setContextMenu(Menu.buildFromTemplate(menuTemplate()));
  tray.on('double-click', openPanel);

  initUpdater();
  // свежий код из установщика — только в уже подготовленную папку (новую заполнит окно подготовки)
  if (backendReady()) { try { syncBackend(); } catch (e) { mlog('syncBackend: ' + e); } }
  mlog(`backendReady=${backendReady()}`);
  if (!backendReady()) { openSetup(); return; }
  await startMascot();
});

async function startMascot() {
  if (!(await ensureBackend())) {
    if (server) { spawn('taskkill', ['/pid', String(server.pid), '/T', '/F'], { windowsHide: true }); server = null; }
    let tail = '';
    try { tail = fs.readFileSync(path.join(ROOT, 'data', 'server.log'), 'utf-8').split(/\r?\n/).filter(Boolean).slice(-6).join('\n'); } catch {}
    mlog('сервер не запустился: ' + tail);
    if (PACKAGED) {
      // вместо тупика — окно подготовки: оно проверит и пересоберёт окружение
      startProblem = 'Сервер Юки не запустился. Нажми «Починить» — я проверю и доставлю всё нужное.\n\n' + tail;
      openSetup();
      return;
    }
    const { dialog, shell } = require('electron');
    dialog.showErrorBox('Юки', `Сервер не запустился. Подробности в файле:\n${path.join(ROOT, 'data', 'server.log')}`);
    shell.openPath(path.join(ROOT, 'data'));
    app.quit();
    return;
  }
  startProblem = '';
  createMascot();
}

app.on('second-instance', openPanel);
app.on('window-all-closed', () => {}); // живём в трее
app.on('before-quit', () => {
  saveState();
  // python.exe из venv — лаунчер с дочерним процессом, поэтому гасим всё дерево
  if (server) spawn('taskkill', ['/pid', String(server.pid), '/T', '/F'], { windowsHide: true });
});
