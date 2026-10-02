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
