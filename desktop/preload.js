// Runs in the dashboard page with context isolation on: exposes only a tiny, safe API.
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('codonova', {
  isDesktop: true,
  // Show a native OS notification (title/body are validated in the main process).
  notify: (title, body) => ipcRenderer.send('notify', { title: String(title), body: String(body) }),
})
