// Constellation Setup — Debug Console log ring (fleet debug-console contract v1).
//
// installConsoleTap() wraps console.log/info/warn/error ONCE in the main process
// so everything the installer logs lands in a ring of the last 600 entries,
// which the wizard's Ctrl+Shift+D overlay reads over IPC (preload.debugState).
// Every line is redacted BEFORE it enters the ring: secrets, the family PIN, and
// anything under a vault folder. The original console output is never broken.
"use strict";

const MAX_ENTRIES = 600;
const MAX_MSG = 2000;

const g = globalThis;
const state = (g.__cstSetupDebugLog ??= { entries: [], tapped: false, lastMs: 0, seq: 0 });

// eslint-disable-next-line no-control-regex
const ANSI = /\x1b\[[0-9;?]*[ -/]*[@-~]/g;
const SECRET = /\b([\w.-]*(?:token|key|secret|pass|authorization)[\w.-]*|pin)(["']?\s*[=:]\s*)(?:bearer\s+)?("[^"]*"|'[^']*'|\S+)/gi;
const BEARER = /\bbearer\s+[A-Za-z0-9._~+/=-]{6,}/gi;
const VAULT = /(?:[A-Za-z]:)?[\\/][^\s"']*?[\\/]vault(?:\.img)?(?:[\\/][^\s"')\],]*)?/gi;
const MAPPER = /\/dev\/mapper\/\S+/g;

// the family PIN travels only on stdin; if a value we know is the PIN is ever
// logged anyway, it is scrubbed by value as well as by pattern
const known = new Set();
function forgetKnown() { known.clear(); }
function rememberSecret(v) { if (typeof v === "string" && v.length >= 4) known.add(v); }

function redact(msg) {
  let s = String(msg);
  for (const v of known) s = s.split(v).join("[redacted]");
  s = s.replace(VAULT, "[vault path]").replace(MAPPER, "[vault device]");
  s = s.replace(BEARER, "Bearer [redacted]");
  s = s.replace(SECRET, (_m, k, sep) => `${k}${sep}[redacted]`);
  return s;
}

function fmt(args) {
  return args.map((a) => {
    if (typeof a === "string") return a;
    if (a instanceof Error) return a.stack || String(a);
    try { return JSON.stringify(a); } catch { return String(a); }
  }).join(" ");
}

function isoNow() {
  // strictly increasing, so ?since=<last t> never drops a same-ms line
  let ms = Date.now();
  if (ms <= state.lastMs) { state.seq += 1; ms = state.lastMs; } else { state.seq = 0; state.lastMs = ms; }
  return new Date(ms).toISOString().replace("Z", String(state.seq).padStart(3, "0") + "Z");
}

function push(level, msg) {
  try {
    if (!["log", "warn", "error"].includes(level)) level = "log";
    const text = redact(String(msg).replace(ANSI, "").replace(/\s+$/, "")).slice(0, MAX_MSG);
    if (!text.trim()) return;
    state.entries.push({ t: isoNow(), level, msg: text });
    if (state.entries.length > MAX_ENTRIES) state.entries.splice(0, state.entries.length - MAX_ENTRIES);
  } catch { /* never break logging */ }
}

function installConsoleTap(target = console) {
  if (state.tapped) return;
  state.tapped = true;
  for (const [method, level] of [["log", "log"], ["info", "log"], ["warn", "warn"], ["error", "error"]]) {
    const orig = target[method].bind(target);
    target[method] = (...args) => {
      try { push(level, fmt(args)); } catch { /* never break logging */ }
      orig(...args);
    };
  }
  push("log", "[debug] console tap installed — installer log mirrors to the Debug Console (Ctrl+Shift+D)");
}

function entries(since) {
  if (!since) return state.entries.slice();
  return state.entries.filter((e) => e.t > since);
}

function clear() { state.entries.length = 0; }

module.exports = { MAX_ENTRIES, MAX_MSG, redact, push, installConsoleTap, entries, clear,
  rememberSecret, forgetKnown, _state: state };
