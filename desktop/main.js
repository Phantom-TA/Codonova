/**
 * Codonova desktop app (Electron main process)
 *
 * Startup flow: check Docker -> `docker compose up -d` -> wait for the backend
 * health check -> open the dashboard in a native window.
 * Desktop extras: tray icon, application menu, native notifications and save
 * dialogs, and a "stop the services?" prompt on quit.
 */
const {
  app, BrowserWindow, Menu, Tray, dialog, shell, Notification, ipcMain, nativeImage, session,
} = require('electron')
const path = require('path')
const fs = require('fs')
const http = require('http')
const { spawn } = require('child_process')

const DASHBOARD_URL = 'http://localhost:3000'
const API_URL = 'http://localhost:8000'
const HEALTH_URL = `${API_URL}/health`
const NEO4J_URL = 'http://localhost:7474'
const REPO_URL = 'https://github.com/Phantom-TA/Codonova'
const ICON = path.join(__dirname, 'assets', 'icon.png')

let splash = null
let mainWindow = null
let tray = null
let projectDir = null
let quitting = false
let servicesStartedHere = false

// ─────────────────────────────────────────────────────────────────────────────
// Single instance
// ─────────────────────────────────────────────────────────────────────────────
if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', () => showMainWindow())
}

// ─────────────────────────────────────────────────────────────────────────────
// Config: remember where the Codonova project (docker-compose.yml) lives
// ─────────────────────────────────────────────────────────────────────────────
const configPath = () => path.join(app.getPath('userData'), 'config.json')

function readConfig() {
  try { return JSON.parse(fs.readFileSync(configPath(), 'utf8')) } catch { return {} }
}

function writeConfig(patch) {
  fs.mkdirSync(path.dirname(configPath()), { recursive: true })
  fs.writeFileSync(configPath(), JSON.stringify({ ...readConfig(), ...patch }, null, 2))
}

const isProjectDir = dir => !!dir && fs.existsSync(path.join(dir, 'docker-compose.yml'))

async function resolveProjectDir() {
  const candidates = [
    readConfig().projectDir,
    process.env.CODONOVA_HOME,
    path.resolve(__dirname, '..'), // running from the repository (desktop/ is a subfolder)
  ]
  for (const dir of candidates) if (isProjectDir(dir)) return dir

  // Installed app: ask the user once and remember the answer.
  for (;;) {
    const result = await dialog.showOpenDialog({
      title: 'Select the Codonova project folder',
      message: 'Choose the folder that contains docker-compose.yml',
      properties: ['openDirectory'],
    })
    if (result.canceled || !result.filePaths[0]) return null
    if (isProjectDir(result.filePaths[0])) {
      writeConfig({ projectDir: result.filePaths[0] })
      return result.filePaths[0]
    }
    await dialog.showMessageBox({
      type: 'warning',
      message: 'That folder does not contain docker-compose.yml.',
      detail: 'Pick the Codonova repository folder.',
    })
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────
function run(cmd, args, { cwd, onLine } = {}) {
  return new Promise(resolve => {
    let out = ''
    let child
    try {
      child = spawn(cmd, args, { cwd, windowsHide: true })
    } catch (error) {
      return resolve({ code: -1, out: String(error), missing: true })
    }
    const feed = chunk => {
      const text = chunk.toString()
      out += text
      if (onLine) text.split(/\r?\n/).filter(Boolean).forEach(onLine)
    }
    child.stdout.on('data', feed)
    child.stderr.on('data', feed)
    child.on('error', error => resolve({ code: -1, out: String(error), missing: error.code === 'ENOENT' }))
    child.on('close', code => resolve({ code, out }))
  })
}

function httpOk(url, timeout = 3000) {
  return new Promise(resolve => {
    const req = http.get(url, { timeout }, res => {
      res.resume()
      resolve(res.statusCode === 200)
    })
    req.on('timeout', () => { req.destroy(); resolve(false) })
    req.on('error', () => resolve(false))
  })
}

async function waitFor(url, timeoutMs, onTick) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await httpOk(url)) return true
    if (onTick) onTick(Math.round((deadline - Date.now()) / 1000))
    await new Promise(r => setTimeout(r, 2000))
  }
  return false
}

const sleep = ms => new Promise(r => setTimeout(r, ms))

// ─────────────────────────────────────────────────────────────────────────────
// Splash
// ─────────────────────────────────────────────────────────────────────────────
function createSplash() {
  splash = new BrowserWindow({
    width: 480, height: 340, frame: false, resizable: false, show: false, center: true,
    backgroundColor: '#080d1a', icon: ICON, skipTaskbar: false, alwaysOnTop: true,
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false },
  })
  splash.loadFile(path.join(__dirname, 'splash.html'))
  splash.once('ready-to-show', () => splash.show())
  return new Promise(resolve => splash.webContents.once('did-finish-load', resolve))
}

function splashStep(id, state, detail) {
  if (!splash || splash.isDestroyed()) return
  splash.webContents
    .executeJavaScript(`setStep(${JSON.stringify(id)}, ${JSON.stringify(state)}, ${JSON.stringify(detail ?? '')})`)
    .catch(() => {})
}

function splashDetail(text, isError = false) {
  if (!splash || splash.isDestroyed()) return
  splash.webContents.executeJavaScript(`setDetail(${JSON.stringify(text)}, ${isError})`).catch(() => {})
}

function closeSplash() {
  if (splash && !splash.isDestroyed()) splash.close()
  splash = null
}

// ─────────────────────────────────────────────────────────────────────────────
// Startup flow
// ─────────────────────────────────────────────────────────────────────────────
async function ensureDocker() {
  splashStep('docker', 'active', 'Checking that Docker is running…')
  let info = await run('docker', ['info'])
  if (info.code === 0) return true

  if (info.missing) {
    splashStep('docker', 'error', 'Docker is not installed.')
    await dialog.showMessageBox({
      type: 'error', message: 'Docker is not installed',
      detail: 'Codonova runs its services (Neo4j, ChromaDB, backend) in Docker.\nInstall Docker Desktop and start Codonova again.',
    })
    return false
  }

  // Docker is installed but the engine is not running: try to start Docker Desktop (Windows).
  const dockerDesktop = path.join(process.env['ProgramFiles'] || 'C:\\Program Files', 'Docker', 'Docker', 'Docker Desktop.exe')
  if (process.platform === 'win32' && fs.existsSync(dockerDesktop)) {
    splashDetail('Starting Docker Desktop… this can take a minute.')
    spawn(dockerDesktop, [], { detached: true, stdio: 'ignore' }).unref()
  } else {
    splashDetail('Docker is not running. Start Docker and wait…')
  }
  const deadline = Date.now() + 180000
  while (Date.now() < deadline) {
    await sleep(4000)
    info = await run('docker', ['info'])
    if (info.code === 0) return true
    splashDetail(`Waiting for Docker to start… (${Math.round((deadline - Date.now()) / 1000)}s)`)
  }
  splashStep('docker', 'error', 'Docker did not start in time.')
  await dialog.showMessageBox({
    type: 'error', message: 'Docker did not start',
    detail: 'Start Docker Desktop manually, wait until it says "running", then open Codonova again.',
  })
  return false
}

async function startServices() {
  splashStep('services', 'active', 'Running docker compose up…')
  if (!fs.existsSync(path.join(projectDir, '.env'))) {
    splashStep('services', 'error', 'No .env file found.')
    const { response } = await dialog.showMessageBox({
      type: 'warning', buttons: ['Open folder', 'Quit'], defaultId: 0,
      message: 'Missing .env file',
      detail: 'Copy .env.example to .env in the Codonova folder and add your Gemini API key and a Neo4j password. Then start Codonova again.',
    })
    if (response === 0) shell.openPath(projectDir)
    return false
  }
  const result = await run('docker', ['compose', 'up', '-d'], {
    cwd: projectDir,
    onLine: line => splashDetail(line.slice(0, 110)),
  })
  if (result.code !== 0) {
    splashStep('services', 'error', 'docker compose failed.')
    await dialog.showMessageBox({
      type: 'error', message: 'Could not start the Codonova services',
      detail: result.out.split('\n').slice(-12).join('\n'),
    })
    return false
  }
  servicesStartedHere = true
  return true
}

async function waitForBackend() {
  splashStep('backend', 'active', 'Waiting for the backend to be ready…')
  const ok = await waitFor(HEALTH_URL, 180000, left => splashDetail(`Waiting for the backend… (${left}s left)`))
  if (!ok) {
    splashStep('backend', 'error', 'The backend did not become ready.')
    const { response } = await dialog.showMessageBox({
      type: 'error', buttons: ['Show logs', 'Quit'], defaultId: 0,
      message: 'The backend did not respond',
      detail: `${HEALTH_URL} did not answer within 3 minutes.`,
    })
    if (response === 0) await showLogs()
    return false
  }
  return true
}

async function launch() {
  await createSplash()
  projectDir = await resolveProjectDir()
  if (!projectDir) return app.quit()

  if (!(await ensureDocker())) return app.quit()
  if (!(await startServices())) return app.quit()
  if (!(await waitForBackend())) return app.quit()

  splashStep('ui', 'active', 'Opening the dashboard…')
  createMainWindow()
}

// ─────────────────────────────────────────────────────────────────────────────
// Main window
// ─────────────────────────────────────────────────────────────────────────────
function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1480, height: 920, minWidth: 1100, minHeight: 680, show: false,
    title: 'Codonova', icon: ICON, backgroundColor: '#080d1a',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true, nodeIntegration: false, sandbox: true,
    },
  })

  mainWindow.once('ready-to-show', () => {
    closeSplash()
    mainWindow.show()
  })
  mainWindow.webContents.on('did-fail-load', (_e, code, desc, url, isMainFrame) => {
    if (!isMainFrame || code === -3) return // -3 = aborted navigation
    dialog.showMessageBox(mainWindow, {
      type: 'error', buttons: ['Retry', 'Close'], defaultId: 0,
      message: 'The dashboard could not be loaded', detail: `${desc} (${url})`,
    }).then(({ response }) => { if (response === 0) mainWindow.loadURL(DASHBOARD_URL) })
  })

  // Anything that is not the dashboard itself (Neo4j, LangSmith, generated app tabs) opens in the browser.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    openExternal(url)
    return { action: 'deny' }
  })
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith(DASHBOARD_URL)) {
      event.preventDefault()
      openExternal(url)
    }
  })

  // Closing the window keeps Codonova in the tray; quit from the tray or File menu.
  mainWindow.on('close', event => {
    if (!quitting) {
      event.preventDefault()
      mainWindow.hide()
      if (!app.__hintShown && Notification.isSupported()) {
        app.__hintShown = true
        new Notification({ title: 'Codonova is still running', body: 'It stays in the system tray. Right-click the icon to quit.', icon: ICON }).show()
      }
    }
  })
  mainWindow.on('closed', () => { mainWindow = null })

  mainWindow.loadURL(DASHBOARD_URL)
}

function showMainWindow() {
  if (!mainWindow) return
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.show()
  mainWindow.focus()
}

function openExternal(url) {
  try {
    const { protocol } = new URL(url)
    if (protocol === 'http:' || protocol === 'https:') shell.openExternal(url)
  } catch { /* ignore malformed URLs */ }
}

// ─────────────────────────────────────────────────────────────────────────────
// Services actions (menu / tray)
// ─────────────────────────────────────────────────────────────────────────────
function toast(title, body) {
  if (Notification.isSupported()) new Notification({ title, body, icon: ICON }).show()
}

async function serviceAction(label, args) {
  toast('Codonova', `${label}…`)
  const result = await run('docker', args, { cwd: projectDir })
  if (result.code === 0) toast('Codonova', `${label}: done`)
  else dialog.showMessageBox({ type: 'error', message: `${label} failed`, detail: result.out.split('\n').slice(-12).join('\n') })
}

async function showLogs() {
  const result = await run('docker', ['compose', 'logs', '--tail', '200', '--no-color', 'backend'], { cwd: projectDir })
  const win = new BrowserWindow({ width: 980, height: 640, title: 'Codonova - backend logs', icon: ICON, autoHideMenuBar: true })
  const escaped = result.out.replace(/&/g, '&amp;').replace(/</g, '&lt;')
  const html = `<!doctype html><meta charset="utf-8"><body style="margin:0;background:#0b1020;color:#cbd5e1">
    <pre style="margin:0;padding:16px;font:12px/1.5 Consolas,monospace;white-space:pre-wrap">${escaped}</pre></body>`
  win.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html))
}

async function chooseProjectFolder() {
  const result = await dialog.showOpenDialog({
    title: 'Select the Codonova project folder', properties: ['openDirectory'],
  })
  if (result.canceled || !result.filePaths[0]) return
  if (!isProjectDir(result.filePaths[0])) {
    return dialog.showMessageBox({ type: 'warning', message: 'That folder does not contain docker-compose.yml.' })
  }
  writeConfig({ projectDir: result.filePaths[0] })
  projectDir = result.filePaths[0]
  toast('Codonova', 'Project folder saved. Restart the app to use it.')
}

function buildMenu() {
  const template = [
    { label: 'File', submenu: [
      { label: 'Choose Codonova folder…', click: chooseProjectFolder },
      { type: 'separator' },
      { label: 'Quit', accelerator: 'CmdOrCtrl+Q', click: () => app.quit() },
    ] },
    { label: 'View', submenu: [
      { role: 'reload' }, { role: 'forceReload' }, { role: 'toggleDevTools' }, { type: 'separator' },
      { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' }, { type: 'separator' }, { role: 'togglefullscreen' },
    ] },
    { label: 'Services', submenu: [
      { label: 'Start services', click: () => serviceAction('Starting services', ['compose', 'up', '-d']) },
      { label: 'Stop services', click: () => serviceAction('Stopping services', ['compose', 'stop']) },
      { label: 'Restart backend', click: () => serviceAction('Restarting the backend', ['compose', 'restart', 'backend']) },
      { type: 'separator' },
      { label: 'Show backend logs', click: showLogs },
      { label: 'API documentation', click: () => openExternal(`${API_URL}/docs`) },
      { label: 'Neo4j Browser', click: () => openExternal(NEO4J_URL) },
      { label: 'LangSmith traces', click: () => openExternal('https://smith.langchain.com/') },
    ] },
    { label: 'Help', submenu: [
      { label: 'Project on GitHub', click: () => openExternal(REPO_URL) },
      { label: 'About Codonova', click: () => dialog.showMessageBox({
        type: 'info', title: 'About Codonova', message: `Codonova ${app.getVersion()}`,
        detail: 'Autonomous software development with deep AI agents, a Neo4j knowledge graph and ChromaDB memory.\n\n' +
                `Electron ${process.versions.electron}  ·  Chromium ${process.versions.chrome}`,
      }) },
    ] },
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

function createTray() {
  const image = nativeImage.createFromPath(ICON).resize({ width: 18, height: 18 })
  tray = new Tray(image)
  tray.setToolTip('Codonova')
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Open Codonova', click: showMainWindow },
    { label: 'API documentation', click: () => openExternal(`${API_URL}/docs`) },
    { label: 'Neo4j Browser', click: () => openExternal(NEO4J_URL) },
    { type: 'separator' },
    { label: 'Quit', click: () => app.quit() },
  ]))
  tray.on('click', showMainWindow)
}

// ─────────────────────────────────────────────────────────────────────────────
// Notifications from the dashboard (preload -> main), downloads, quit prompt
// ─────────────────────────────────────────────────────────────────────────────
ipcMain.on('notify', (event, payload) => {
  const url = event.senderFrame && event.senderFrame.url
  if (!url || !url.startsWith(DASHBOARD_URL)) return // only our own dashboard may notify
  const title = String(payload?.title || '').slice(0, 80)
  const body = String(payload?.body || '').slice(0, 240)
  if (!title || !Notification.isSupported()) return
  const note = new Notification({ title, body, icon: ICON })
  note.on('click', showMainWindow)
  note.show()
})

function watchDownloads() {
  // Electron shows a native "Save as" dialog for the ZIP export; tell the user when it is saved.
  session.defaultSession.on('will-download', (_event, item) => {
    item.once('done', (_e, state) => {
      if (state === 'completed') toast('Download complete', path.basename(item.getSavePath() || item.getFilename()))
    })
  })
}

app.on('before-quit', async event => {
  if (quitting) return
  event.preventDefault()
  if (mainWindow && !mainWindow.isVisible()) mainWindow.show()
  const { response } = await dialog.showMessageBox(mainWindow || undefined, {
    type: 'question', buttons: ['Keep services running', 'Stop services', 'Cancel'],
    defaultId: 0, cancelId: 2, title: 'Quit Codonova',
    message: 'Stop the Codonova services too?',
    detail: 'The services (Neo4j, ChromaDB, backend, frontend) run in Docker. Keeping them running makes the next start instant.',
  })
  if (response === 2) return
  if (response === 1 && projectDir) {
    if (mainWindow) mainWindow.hide()
    toast('Codonova', 'Stopping services…')
    await run('docker', ['compose', 'stop'], { cwd: projectDir })
  }
  quitting = true
  app.quit()
})

app.on('window-all-closed', () => { /* stay in the tray */ })

app.whenReady().then(async () => {
  buildMenu()
  createTray()
  watchDownloads()
  await launch()
})
