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
  // "Where are your photos?" — read-only discovery + a summary of a picked folder
  scanPhotos: (opts) => ipcRenderer.invoke("scanPhotos", opts || {}),
  summarizeFolder: (dir, opts) => ipcRenderer.invoke("summarizeFolder", dir, opts || {}),
  onScanProgress: (cb) => ipcRenderer.on("scanProgress", (_e, p) => cb(p)),
  libraryCheck: (cfg) => ipcRenderer.invoke("libraryCheck", cfg),
  enableDescribe: (cfg) => ipcRenderer.invoke("enableDescribe", cfg),
  // Debug Console: read-only log tail + status checks (main-process log ring)
  debugState: (since) => ipcRenderer.invoke("debugState", typeof since === "string" ? since : ""),
});
