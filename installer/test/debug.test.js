"use strict";
// Installer Debug Console: the main-process log ring, the state builder the
// wizard's Ctrl+Shift+D overlay reads over IPC, the sender gate on that IPC,
// and the wiring in main.js / preload.js / ui (static checks — no Electron here).
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const net = require("net");
const os = require("os");
const path = require("path");
const { pathToFileURL } = require("url");
const debuglog = require("../debuglog");
const debugstate = require("../debugstate");

const ROOT = path.resolve(__dirname, "..");
const UI_INDEX = path.join(ROOT, "ui", "index.html");

test("ring: schema, bounded, ANSI stripped, 2000-char cap", () => {
  debuglog.clear();
  for (let i = 0; i < debuglog.MAX_ENTRIES + 25; i++) debuglog.push("log", `\x1b[31mline ${i}\x1b[0m`);
  const e = debuglog.entries();
  assert.equal(e.length, debuglog.MAX_ENTRIES);
  assert.equal(e.at(-1).msg, `line ${debuglog.MAX_ENTRIES + 24}`);
  for (const x of e.slice(-3)) {
    assert.deepEqual(Object.keys(x).sort(), ["level", "msg", "t"]);
    assert.match(x.t, /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/);
  }
  debuglog.push("warn", "y".repeat(5000));
  assert.equal(debuglog.entries().at(-1).msg.length, 2000);
  assert.equal(debuglog.entries().at(-1).level, "warn");
});

test("ring: since= tails strictly, even within one millisecond", () => {
  debuglog.clear();
  debuglog.push("log", "A");
  const tA = debuglog.entries().at(-1).t;
  debuglog.push("log", "B");
  debuglog.push("error", "C");
  assert.deepEqual(debuglog.entries(tA).map((x) => x.msg), ["B", "C"]);
  assert.deepEqual(debuglog.entries(debuglog.entries().at(-1).t), []);
});

test("redaction: planted secrets, the PIN and vault paths never enter the ring", () => {
  debuglog.clear();
  debuglog.rememberSecret("7391");
  const lines = [
    "ollama token=tok3nV4lueZZ ok",
    "SMTP_PASS=hunter2smtp",
    "Authorization: Bearer B3arerOpaque99",
    '{"pin":"7391"}',
    "the family PIN is 7391 (should never be logged, but if it is)",
    "moved /home/user/Constellation/vault/flagged/IMG_0042.jpg",
    "C:\\Users\\me\\Constellation\\vault\\review\\IMG_0043.jpg copied",
    "luks /home/user/Constellation/library/vault.img -> /dev/mapper/memoryvault",
  ];
  for (const l of lines) debuglog.push("log", l);
  const text = JSON.stringify(debuglog.entries());
  for (const s of ["tok3nV4lueZZ", "hunter2smtp", "B3arerOpaque99", "7391", "IMG_0042", "IMG_0043",
    "vault.img", "/dev/mapper/memoryvault"]) {
    assert.ok(!text.includes(s), `leaked: ${s} in ${text}`);
  }
  assert.ok(text.includes("[redacted]"));
  assert.ok(text.includes("[vault path]"));
  assert.ok(debuglog.entries()[0].msg.startsWith("ollama token="));
  debuglog.forgetKnown();
});

test("console tap: mirrors every level once, never breaks the original", () => {
  debuglog.clear();
  const seen = [];
  const fake = { log: (...a) => seen.push(["log", ...a]), info: (...a) => seen.push(["info", ...a]),
    warn: (...a) => seen.push(["warn", ...a]), error: (...a) => seen.push(["error", ...a]) };
  const st = debuglog._state;
  const was = st.tapped;
  st.tapped = false;
  try {
    debuglog.installConsoleTap(fake);
    debuglog.installConsoleTap(fake);          // idempotent
    fake.log("hello", { a: 1 });
    fake.warn("careful password=abc123xyz");
    fake.error(new Error("kaboom"));
  } finally { st.tapped = was; }
  assert.deepEqual(seen.map((x) => x[0]), ["log", "warn", "error"]);
  assert.equal(seen[1][1], "careful password=abc123xyz");   // original untouched
  const e = debuglog.entries();
  assert.equal(e.filter((x) => x.msg.includes("tap installed")).length, 1);
  assert.ok(e.some((x) => x.level === "log" && x.msg === 'hello {"a":1}'));
  assert.ok(e.some((x) => x.level === "warn" && x.msg === "careful password=[redacted]"));
  assert.ok(e.some((x) => x.level === "error" && x.msg.includes("kaboom")));
});

test("state: contract schema, checks shaped, answers < 3 s against a hung Ollama", async () => {
  debuglog.clear();
  debuglog.push("log", "state probe line");
  const srv = net.createServer(() => { /* accept, never answer */ }).listen(0, "127.0.0.1");
  await new Promise((r) => srv.once("listening", r));
  const port = srv.address().port;
  const t0 = Date.now();
  try {
    const d = await debugstate.buildState({
      backendDir: "/nonexistent", dataDir: path.join(os.tmpdir(), "cst-dbg-nope"),
      exeFn: () => null, ollamaUrl: `http://127.0.0.1:${port}/api/version`,
    });
    assert.ok(Date.now() - t0 < 3000, `took ${Date.now() - t0} ms`);
    assert.equal(d.product, "constellation-setup");
    assert.match(d.now, /Z$/);
    assert.ok(d.log.some((e) => e.msg === "state probe line"));
    assert.ok(d.checks.length >= 3 && d.checks.length <= 8);
    for (const c of d.checks) {
      assert.deepEqual(Object.keys(c).sort(), ["detail", "label", "ok"]);
      assert.ok([true, false, null].includes(c.ok));
    }
    const by = Object.fromEntries(d.checks.map((c) => [c.label, c]));
    assert.notEqual(by.Ollama.ok, true);
    assert.equal(by["Backend bundle"].ok, false);
    const later = await debugstate.buildState({ since: d.log.at(-1).t, backendDir: "/x",
      dataDir: os.tmpdir(), exeFn: () => null, ollamaUrl: `http://127.0.0.1:9/api/version` });
    assert.deepEqual(later.log, []);
  } finally { srv.close(); }
});

test("IPC gate: only the bundled wizard page may read the log", () => {
  const good = pathToFileURL(UI_INDEX).href;
  assert.equal(debugstate.isTrustedSender(good, UI_INDEX), true);
  assert.equal(debugstate.isTrustedSender(good + "#step3", UI_INDEX), true);
  for (const bad of ["", null, undefined, "https://evil.example/", "http://127.0.0.1:8484/",
    pathToFileURL(path.join(ROOT, "ui", "other.html")).href, "file:///tmp/index.html"]) {
    assert.equal(debugstate.isTrustedSender(bad, UI_INDEX), false, String(bad));
  }
});

test("wiring: main taps + gates the IPC, preload bridges it, wizard loads the overlay, build ships it", () => {
  const main = fs.readFileSync(path.join(ROOT, "main.js"), "utf8");
  const preload = fs.readFileSync(path.join(ROOT, "preload.js"), "utf8");
  const html = fs.readFileSync(UI_INDEX, "utf8");
  const ui = fs.readFileSync(path.join(ROOT, "ui", "debug.js"), "utf8");
  const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8"));
  assert.match(main, /debuglog\.installConsoleTap\(\)/);
  assert.match(main, /ipcMain\.handle\("debugState"[\s\S]*isTrustedSender\(url, UI_INDEX\)[\s\S]*forbidden/);
  assert.match(main, /rememberSecret\(String\(cfg\.pin\)\)/);
  assert.match(main, /contextIsolation: true, nodeIntegration: false/);
  assert.match(preload, /debugState: \(since\) => ipcRenderer\.invoke\("debugState"/);
  assert.match(html, /<script src="debug\.js"><\/script>/);
  assert.match(ui, /e\.ctrlKey && e\.shiftKey/);
  assert.match(ui, /window\.setup && window\.setup\.debugState/);
  assert.ok(!/innerHTML|require\(|ipcRenderer/.test(ui), "renderer overlay must stay DOM-text-only, no Node");
  for (const f of ["debuglog.js", "debugstate.js"]) assert.ok(pkg.build.files.includes(f), `${f} not packaged`);
});
