const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('yukiSetup', {
  info: () => ipcRenderer.invoke('setup-info'),
  pickDir: () => ipcRenderer.invoke('setup-pick-dir'),
  open: (what) => ipcRenderer.send('setup-open', what),
  start: (opts) => ipcRenderer.send('setup-start', opts),
  launch: () => ipcRenderer.send('setup-launch'),
  window: (cmd) => ipcRenderer.send('setup-window', cmd),
  onEvent: (cb) => ipcRenderer.on('setup-event', (_e, m) => cb(m)),
});
