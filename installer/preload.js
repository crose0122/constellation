// Constellation Setup — secure IPC bridge (context-isolated).
"use strict";
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("setup", {
  scan: () => ipcRenderer.invoke("scan"),
  pickFolder: (title) => ipcRenderer.invoke("pickFolder", title),
  prepare: (cfg) => ipcRenderer.invoke("prepare", cfg),
  install: (cfg) => ipcRenderer.invoke("install", cfg),
  sweep: (cfg) => ipcRenderer.invoke("sweep", cfg),
  finish: (cfg) => ipcRenderer.invoke("finish", cfg),
  lanAddress: () => ipcRenderer.invoke("lanAddress"),
  defaults: () => ipcRenderer.invoke("defaults"),
  openWall: () => ipcRenderer.invoke("openWall"),
  onProgress: (cb) => ipcRenderer.on("progress", (_e, p) => cb(p)),
  // Debug Console: read-only log tail + status checks (main-process log ring)
  debugState: (since) => ipcRenderer.invoke("debugState", typeof since === "string" ? since : ""),
});
