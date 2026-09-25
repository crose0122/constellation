// Constellation Setup — Electron main process.
// Owns the wizard window and bridges the renderer to the scan/setup engines.
"use strict";
const { app, BrowserWindow, ipcMain, dialog, shell } = require("electron");
const path = require("path");
const os = require("os");
const scan = require("./scan");
const setup = require("./setup");
const autostart = require("./autostart");
const decide = require("./decide");
const DATA_DIR = () => path.join(app.getPath("home"), "Constellation");

let win;
const APP_DIR = app.isPackaged ? path.dirname(app.getPath("exe")) : __dirname;
// the PyInstaller backend bundle: shipped as extraResources when packaged,
// or the local build output in dev
const BACKEND_DIR = app.isPackaged
  ? path.join(process.resourcesPath, "backend")
  : path.join(__dirname, "..", "scripts", "dist", "memoryvault-brain");

function createWindow() {
  win = new BrowserWindow({
    width: 940, height: 680, minWidth: 820, minHeight: 600,
    backgroundColor: "#010409", title: "Constellation Setup",
    autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, "preload.js"),
      contextIsolation: true, nodeIntegration: false },
  });
  win.loadFile(path.join(__dirname, "ui", "index.html"));
}

app.whenReady().then(createWindow);
app.on("window-all-closed", () => app.quit());

// --- IPC: hardware/network scan -------------------------------------------
ipcMain.handle("scan", async () => {
  try { return { ok: true, data: await scan.fullScan() }; }
  catch (e) { return { ok: false, error: String(e) }; }
});

// --- IPC: pick a folder (library location / a source) ----------------------
ipcMain.handle("pickFolder", async (_e, title) => {
  const r = await dialog.showOpenDialog(win,
    { title: title || "Choose a folder", properties: ["openDirectory", "createDirectory"] });
  return r.canceled ? null : r.filePaths[0];
});

// --- IPC: library + family PIN + family certificate ------------------------
ipcMain.handle("prepare", async (_e, cfg) => {
  try { return await setup.prepare(BACKEND_DIR, DATA_DIR(), cfg); }
  catch (e) { return { ok: false, error: String((e && e.message) || e) }; }
});

// --- IPC: install Ollama + pull the model (streams progress) ---------------
ipcMain.handle("install", async (_e, cfg) => {
  const send = (p) => win && win.webContents.send("progress", p);
  try {
    await setup.installOllama(cfg, send);
    await setup.pullModel(cfg.model, send);
    return { ok: true };
  } catch (e) {
    send({ phase: "error", msg: String(e && e.message || e) });
    return { ok: false, error: String(e) };
  }
});

// --- IPC: write config + run the first sweep -------------------------------
// This is what puts photos in the library. Without it the wizard finishes onto
// an empty star map and the folders chosen on the storage step are never read.
ipcMain.handle("sweep", async (_e, cfg) => {
  const send = (p) => win && win.webContents.send("progress", p);
  try {
    setup.writeConfig(DATA_DIR(), cfg);
    return await setup.runFirstSweep(BACKEND_DIR, cfg, send);
  } catch (e) {
    send({ phase: "error", msg: String((e && e.message) || e) });
    return { ok: false, error: String(e) };
  }
});

// --- IPC: launch the stack, start the slow stages, open the app ------------
ipcMain.handle("finish", async (_e, cfg) => {
  const send = (p) => win && win.webContents.send("progress", p);
  try {
    const envFile = setup.writeConfig(DATA_DIR(), cfg);
    const exe = setup.backendExe(BACKEND_DIR);
    // Start on boot (systemd user unit / Scheduled Task). If that works it
    // also starts the server now; otherwise fall back to a one-off launch so
    // the family still sees their sky tonight, and say so.
    let auto = { ok: false, error: "no bundled backend" };
    if (exe) auto = await autostart.install({ exe, envFile, dataDir: DATA_DIR() });
    let launched = false;
    if (auto.ok && auto.started) {
      launched = true;                       // verified by autostart (unit verify + enable / task + Run)
    } else if (!auto.ok) {
      send({ phase: "launch", msg: `Start-on-boot not set up (${auto.error}); starting just for now.` });
      setup.launchStack(BACKEND_DIR, APP_DIR, cfg, send);
      launched = true;                       // one-off launch attempted; verified below by the probe
    }
    // the model-bound stages continue after this window closes
    const bg = setup.startBackgroundSweep(BACKEND_DIR, cfg);
    // Startup claims are earned: ask the server before the wizard says anything
    // is running. When autostart just started it, give it a few seconds to bind.
    const lan = lanAddress();
    const port = 8484;
    let serverUp = false;
    if (launched) serverUp = await setup.serverUp("127.0.0.1", port, 15000);
    const claims = decide.finishClaims({
      autostartOk: !!auto.ok,
      started: !!auto.started || launched,
      serverUp,
      background: !!bg.ok,
    });
    return { ok: claims.ok, autostart: !!auto.ok, background: !!bg.ok,
      serverUp, autostartError: auto.ok ? null : String(auto.error || ""), ...claims };
  } catch (e) { return { ok: false, error: String((e && e.message) || e) }; }
});

// --- IPC: this machine's LAN address, for the TV / phone step --------------
function lanAddress() {
  for (const ifaces of Object.values(os.networkInterfaces() || {})) {
    for (const i of ifaces || []) {
      if (i.family === "IPv4" && !i.internal) return i.address;
    }
  }
  return null;
}

ipcMain.handle("lanAddress", () => lanAddress());

ipcMain.handle("openUrl", (_e, url) => shell.openExternal(url));
ipcMain.handle("defaults", () => ({
  libraryRoot: path.join(os.homedir(), "Constellation", "library"),
}));
