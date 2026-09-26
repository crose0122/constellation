"use strict";
// setup.js against a FAKE backend executable that records every call, so we
// can prove what the installer sends where — above all, that the PIN only
// ever travels on stdin (never argv, env, the config file or the log).
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const setup = require("../setup");

const PIN = "8317";

function fakeBackend() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "cst-fake-"));
  const rec = path.join(dir, "calls.jsonl");
  const exe = path.join(dir, process.platform === "win32" ? "memoryvault-brain.exe" : "memoryvault-brain");
  // a node script behind a shebang stands in for the PyInstaller binary
  fs.writeFileSync(exe, `#!${process.execPath}
const fs = require("fs");
let stdin = "";
process.stdin.on("data", (b) => (stdin += b));
process.stdin.on("end", () => {
  fs.appendFileSync(${JSON.stringify(rec)}, JSON.stringify({ argv: process.argv.slice(2),
    mv: Object.fromEntries(Object.entries(process.env).filter(([k]) => k.startsWith("MEMORYVAULT_"))),
    envHasPin: Object.values(process.env).some((v) => String(v).includes(${JSON.stringify(PIN)})),
    stdin }) + "\\n");
  if (process.argv[2] === "ingest") {
    for (const d of [25, 50]) console.log(JSON.stringify({ progress: { done: d, total: 60 } }));
    console.log("{'canonical': 60}");
  }
  if (process.env.FAKE_FAIL === process.argv[2]) { console.error("boom: " + process.argv[2]); process.exit(3); }
});
`, { mode: 0o755 });
  const calls = () => fs.readFileSync(rec, "utf8").trim().split("\n").map((l) => JSON.parse(l));
  return { dir, exe, calls };
}

const BACKUP = fs.mkdtempSync(path.join(os.tmpdir(), "cst-backup-"));
const baseCfg = (lib) => ({ libraryRoot: lib, model: "qwen2.5vl:7b", mode: "cpu",
  sources: ["/photos/a"], backupTarget: BACKUP, updates: true, pin: PIN });

test("prepare: init, PIN via stdin only, family cert — in that order", async () => {
  const fb = fakeBackend();
  const data = fs.mkdtempSync(path.join(os.tmpdir(), "cst-data-"));
  const r = await setup.prepare(fb.dir, data, baseCfg(path.join(data, "lib")));
  assert.equal(r.ok, true, r.error);
  const calls = fb.calls();
  assert.deepEqual(calls.map((c) => c.argv.join(" ")), ["init", "pin set --stdin", "tls init"]);
  assert.equal(calls[1].stdin, PIN + "\n", "PIN arrives on stdin");
  for (const c of calls) {
    assert.ok(!c.argv.join(" ").includes(PIN), "PIN never in argv");
    assert.equal(c.envHasPin, false, "PIN never in the environment");
  }
  assert.equal(calls[0].stdin, "", "only the PIN step gets stdin");
});

test("config file: no PIN, 0600, has backup + update choice", async () => {
  const fb = fakeBackend();
  const data = fs.mkdtempSync(path.join(os.tmpdir(), "cst-data-"));
  const r = await setup.prepare(fb.dir, data, baseCfg(path.join(data, "lib")));
  const text = fs.readFileSync(r.envFile, "utf8");
  assert.doesNotMatch(text, new RegExp(PIN));
  assert.doesNotMatch(text, /PIN/i);
  assert.ok(text.split("\n").includes(`MEMORYVAULT_BACKUP_TARGET=${BACKUP}`));
  assert.match(text, /^MEMORYVAULT_AUTO_UPDATE=1$/m);
  assert.match(text, /^OLLAMA_NUM_GPU=0$/m, "CPU mode keeps inference off the GPU");
  if (process.platform !== "win32") assert.equal(fs.statSync(r.envFile).mode & 0o777, 0o600);
});

test("updates off is recorded", () => {
  const lines = setup.configLines({ ...baseCfg("/l"), updates: false });
  assert.ok(lines.includes("MEMORYVAULT_AUTO_UPDATE=0"));
});

test("a folder name with a line break can't inject config lines", () => {
  const data = fs.mkdtempSync(path.join(os.tmpdir(), "cst-data-"));
  assert.throws(() => setup.writeConfig(data, { ...baseCfg("/l\nMEMORYVAULT_VAULT_MODE=off") }), /line break/);
});

test("prepare stops with a plain message when a step fails", async () => {
  const fb = fakeBackend();
  const data = fs.mkdtempSync(path.join(os.tmpdir(), "cst-data-"));
  process.env.FAKE_FAIL = "tls";
  try {
    const r = await setup.prepare(fb.dir, data, baseCfg(path.join(data, "lib")));
    assert.equal(r.ok, false);
    assert.match(r.error, /family certificate/);
  } finally { delete process.env.FAKE_FAIL; }
});

test("prepare with no backend says so plainly", async () => {
  const empty = fs.mkdtempSync(path.join(os.tmpdir(), "cst-none-"));
  const r = await setup.prepare(empty, empty, baseCfg("/l"));
  assert.equal(r.ok, false);
  assert.match(r.error, /program files are missing/);
});

test("first sweep: recent-first, capped at 2,000, counts up, archive backfills overnight", async () => {
  const fb = fakeBackend();
  const data = fs.mkdtempSync(path.join(os.tmpdir(), "cst-data-"));
  const seen = [];
  const r = await setup.runFirstSweep(fb.dir, baseCfg(path.join(data, "lib")), (p) => seen.push(p));
  assert.equal(r.ok, true);
  const ingest = fb.calls().find((c) => c.argv[0] === "ingest");
  assert.deepEqual(ingest.argv, ["ingest", "--recent-first", "--progress-json", "--limit", "2000"]);
  assert.deepEqual(seen.filter((p) => p.count != null).map((p) => p.count), [25, 50]);
  assert.deepEqual(setup.BACKGROUND_STAGES[0], ["ingest"], "overnight chain starts with the backfill");
  const bg = setup.BACKGROUND_STAGES.map((s) => s[0]);
  assert.ok(bg.indexOf("bursts") > bg.indexOf("ingest") && bg.indexOf("bursts") < bg.indexOf("screen"),
    "bursts run after ingest and before screening, so parked frames are never screened or tagged");
  const fg = setup.FOREGROUND_STAGES.map((s) => s.args[0]);
  assert.ok(fg.includes("bursts"), "first sweep culls bursts too, so the first sky isn't 30 copies of one moment");
});

test("progress parser ignores everything that isn't a progress line", () => {
  assert.deepEqual(setup.parseProgress('{"progress":{"done":3,"total":9}}'), { done: 3, total: 9 });
  for (const l of ["", "ingested 50/100", "{'canonical': 60}", '{"progress":{"done":"x"}}', "{bad"])
    assert.equal(setup.parseProgress(l), null, l);
});

test("a stray MEMORYVAULT_* in the parent env can't redirect the PIN or keys", async () => {
  const fb = fakeBackend();
  const data = fs.mkdtempSync(path.join(os.tmpdir(), "cst-data-"));
  process.env.MEMORYVAULT_PIN_FILE = "/tmp/elsewhere/pin.json";
  process.env.MEMORYVAULT_TLS_DIR = "/tmp/elsewhere/tls";
  try {
    const r = await setup.prepare(fb.dir, data, baseCfg(path.join(data, "lib")));
    assert.equal(r.ok, true);
  } finally {
    delete process.env.MEMORYVAULT_PIN_FILE;
    delete process.env.MEMORYVAULT_TLS_DIR;
  }
  const leaked = fs.readFileSync(path.join(fb.dir, "calls.jsonl"), "utf8");
  assert.doesNotMatch(leaked, /elsewhere/);
});

test("the bundled screening model is passed to the backend and the config", async () => {
  const model = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "cst-model-")), "nsfw-screen.onnx");
  fs.writeFileSync(model, "x");
  process.env.CONSTELLATION_SCREEN_MODEL = model;
  try {
    const fb = fakeBackend();
    const data = fs.mkdtempSync(path.join(os.tmpdir(), "cst-data-"));
    const r = await setup.prepare(fb.dir, data, baseCfg(path.join(data, "lib")));
    assert.match(fs.readFileSync(r.envFile, "utf8"), new RegExp(`^MEMORYVAULT_NSFW_ONNX_PATH=${model.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`, "m"));
    assert.equal(fb.calls()[0].mv.MEMORYVAULT_NSFW_ONNX_PATH, model);
  } finally { delete process.env.CONSTELLATION_SCREEN_MODEL; }
});

test("an unwritable library folder is refused in plain words before anything runs", async () => {
  if (process.platform === "win32" || process.getuid() === 0) return;
  const fb = fakeBackend();
  const locked = fs.mkdtempSync(path.join(os.tmpdir(), "cst-locked-"));
  fs.chmodSync(locked, 0o555);
  try {
    const r = await setup.prepare(fb.dir, fs.mkdtempSync(path.join(os.tmpdir(), "cst-data-")),
      baseCfg(path.join(locked, "lib")));
    assert.equal(r.ok, false);
    assert.match(r.error, /can't save files in/);
    assert.doesNotMatch(r.error, /Traceback|PYI|Errno/);
    assert.equal(fs.existsSync(path.join(fb.dir, "calls.jsonl")), false, "backend never called");
  } finally { fs.chmodSync(locked, 0o755); }
});
