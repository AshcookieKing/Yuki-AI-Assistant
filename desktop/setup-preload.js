const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('setup', {
  run: (script) => ipcRenderer.send('setup-run', script),
  ready: () => ipcRenderer.invoke('setup-ready'),
  launch: () => ipcRenderer.send('setup-launch'),
});
