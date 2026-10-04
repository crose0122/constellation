"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const d = require("../decide");
const s = require("../setup");

test("library root: never the home folder, a whole drive, a photo folder or the backup drive", () => {
  const H = "/home/example";
  assert.match(d.validateLibraryRoot(H, { home: H }), /whole home folder/);
  assert.match(d.validateLibraryRoot(H + "/", { home: H }), /whole home folder/);
  assert.match(d.validateLibraryRoot("C:\\Users\\Kim", { home: "c:/users/kim" }), /whole home folder/);
  assert.match(d.validateLibraryRoot("/", {}), /whole drive/);
  assert.match(d.validateLibraryRoot("D:\\", {}), /whole drive/);
  assert.match(d.validateLibraryRoot(H + "/Pictures/lib", { sources: [H + "/Pictures"] }), /overlaps/);
  assert.match(d.validateLibraryRoot(H, { home: "/x", sources: [H + "/Pictures"] }), /overlaps/, "library containing a source");
  assert.match(d.validateLibraryRoot("/media/kim/USB/c", { backupTarget: "/media/kim/USB" }), /backup drive/);
  assert.equal(d.validateLibraryRoot(H + "/Constellation/library",
    { home: H, sources: [H + "/Pictures"], backupTarget: "/media/kim/USB" }), null);
  assert.equal(d.validateLibraryRoot(H + "/Pics2", { sources: [H + "/Pics"] }), null, "a name prefix is not 'inside'");
  assert.match(d.validateLibraryRoot("", {}), /Pick a place/);
});

test("library root: a second path to the home folder is still the home folder", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "lib-guard-"));
  try {
    const home = path.join(tmp, "home");
    const pics = path.join(home, "Pictures");
    fs.mkdirSync(pics, { recursive: true });
    const alias = path.join(tmp, "alias-of-home");
    fs.symlinkSync(home, alias);          // stands in for a bind mount
    const err = (lib, o = {}) => s.libraryLocationError(
      { libraryRoot: lib, sources: o.sources || [], backupTarget: o.backup }, { home });
    assert.match(err(alias), /whole home folder/);
    assert.match(err(path.join(alias, "Pictures", "lib"), { sources: [pics] }), /overlaps/);
    assert.equal(err(path.join(alias, "Constellation", "library"), { sources: [pics] }), null);
  } finally { fs.rmSync(tmp, { recursive: true, force: true }); }
});

// Mutation check (2026-10-04): the device/inode backup-overlap check in
// setup.libraryLocationError had no test — removing it left the whole installer
// suite at its pre-existing pass count. decide.validateLibraryRoot compares
// TEXT only, so a library folder that is a symlink/bind alias of the backup
// drive slips past it and is caught ONLY by the device+inode check here.
test("library root: a second path to the backup drive is still the backup drive", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "bk-guard-"));
  try {
    const drive = path.join(tmp, "USB");
    fs.mkdirSync(path.join(drive, "Family 2019"), { recursive: true });
    const alias = path.join(tmp, "alias-of-usb");
    fs.symlinkSync(drive, alias);                 // stands in for a bind mount
    const err = (lib, backup) => s.libraryLocationError(
      { libraryRoot: lib, sources: [], backupTarget: backup }, { home: path.join(tmp, "home") });
    assert.match(err(path.join(alias, "Constellation", "library"), drive), /backup drive/);
    assert.match(err(path.join(alias, "Constellation"), drive), /backup drive/);
    assert.match(err(path.join(drive, "Constellation", "library"), alias), /backup drive/,
      "a text-only check cannot see the alias; the device/inode check must catch it");
    assert.equal(err(path.join(tmp, "own-drive", "Constellation"), drive), null,
      "two genuinely different folders are not a backup overlap");
  } finally { fs.rmSync(tmp, { recursive: true, force: true }); }
});

test("prepare refuses a bad library location before running anything", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "prep-"));
  try {
    const exe = path.join(tmp, "brain");
    fs.writeFileSync(exe, "#!/bin/sh\nexit 0\n", { mode: 0o755 });
    process.env.CONSTELLATION_BACKEND = exe;
    const r = await s.prepare(tmp, path.join(tmp, "data"),
      { libraryRoot: os.homedir(), sources: [], pin: "4827" });
    assert.equal(r.ok, false);
    assert.equal(r.field, "lib");
    assert.match(r.error, /whole home folder/);
    assert.equal(fs.existsSync(path.join(tmp, "data", ".env")), false, "nothing written");
  } finally { delete process.env.CONSTELLATION_BACKEND; fs.rmSync(tmp, { recursive: true, force: true }); }
});

test("AI descriptions are off unless the family said yes", () => {
  const off = s.configLines({ libraryRoot: "/l", model: "m" }).join("\n");
  assert.match(off, /^MEMORYVAULT_VISION=off$/m);
  const on = s.configLines({ libraryRoot: "/l", model: "m", describe: true }).join("\n");
  assert.match(on, /^MEMORYVAULT_VISION=on$/m);
  assert.equal(s.visionOn({ describe: "yes" }), false, "only an explicit true counts");
  const names = (cfg) => s.backgroundStages(cfg).map((a) => a[0]);
  assert.equal(names({}).includes("tag"), false);
  assert.equal(names({}).includes("describe"), false);
  assert.ok(names({}).includes("screen"), "CPU screening still runs");
  assert.ok(names({ describe: true }).includes("tag"));
  assert.ok(names({ describe: true }).includes("describe"));
});

test("the model download does nothing without the family's yes", async () => {
  // Mutation check (2026-10-02): the install-skip had no test.
  const calls = [];
  const rt = { installOllama: async () => calls.push("ollama"), pullModel: async () => calls.push("model") };
  for (const cfg of [{}, { describe: false }, { describe: "yes" }, { describe: 1 }]) {
    assert.deepEqual(await s.installVision({ ...cfg, model: "m" }, () => {}, rt), { ok: true, skipped: true });
  }
  assert.deepEqual(calls, [], "no Ollama, no model, no graphics card");
  assert.deepEqual(await s.installVision({ describe: true, model: "m" }, () => {}, rt), { ok: true });
  assert.deepEqual(calls, ["ollama", "model"]);
});

test("library root: dot segments are resolved before the check", () => {
  const o = { home: "/home/example" };
  for (const r of ["/.", "/..", "/tmp/..", "/a/../..", "//", "C:\\x\\..\\..", "C:\\..", "c:/x/../..", "C:"]) {
    assert.match(d.validateLibraryRoot(r, o), /whole drive/, r);
  }
  assert.match(d.validateLibraryRoot("/home/example/../example", o), /whole home folder/);
  assert.equal(d.validateLibraryRoot("/home/example/./Pictures/../Constellation", o), null);
});

test("Start my sky needs a valid PIN, a backup choice and no backup error", () => {
  const ok = { pinValid: true, backupChoice: "skip", bkErr: null, backupOther: "" };
  assert.equal(d.startReady(ok), true);
  assert.equal(d.startReady({ ...ok, pinValid: false }), false, "an empty PIN can never be sent");
  assert.equal(d.startReady({ ...ok, backupChoice: null }), false);
  assert.equal(d.startReady({ ...ok, bkErr: "same drive" }), false);
  assert.equal(d.startReady({ ...ok, backupChoice: "other", backupOther: "" }), false);
  assert.equal(d.startReady({ ...ok, backupChoice: "other", backupOther: "/mnt/b" }), true);
});
