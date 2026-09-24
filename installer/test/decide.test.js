"use strict";
// node --test installer/test   (no Electron needed)
const test = require("node:test");
const assert = require("node:assert/strict");
const d = require("../decide");

const drives = [
  { path: "/", freeGB: 40 },
  { path: "/mnt/data", freeGB: 900 },
  { path: "/mnt/elements", freeGB: 3600, removable: true },
];

test("hardware floor: 8 GB RAM and 200 GB free where the library goes", () => {
  assert.equal(d.hardwareFloor({ ramGB: 16 }, drives, "/mnt/data/Constellation").ok, true);
  const lowRam = d.hardwareFloor({ ramGB: 4 }, drives, "/mnt/data/Constellation");
  assert.equal(lowRam.ok, false);
  assert.match(lowRam.problems[0], /4 GB of memory/);
  const lowDisk = d.hardwareFloor({ ramGB: 16 }, drives, "/home/alex/Constellation");
  assert.equal(lowDisk.ok, false, "home is on / with 40 GB — must be refused");
  assert.match(lowDisk.problems[0], /40 GB free/);
  assert.ok(lowDisk.shoppingList);
});

test("exactly at the floor passes; one below fails", () => {
  const at = [{ path: "/", freeGB: 200 }];
  assert.equal(d.hardwareFloor({ ramGB: 8 }, at, "/x").ok, true);
  assert.equal(d.hardwareFloor({ ramGB: 7 }, at, "/x").ok, false);
  assert.equal(d.hardwareFloor({ ramGB: 8 }, [{ path: "/", freeGB: 199 }], "/x").ok, false);
});

test("drive lookup picks the longest matching mount, Windows too", () => {
  assert.equal(d.pickDriveFor("/mnt/data/lib", drives).path, "/mnt/data");
  assert.equal(d.pickDriveFor("/mnt/database", drives).path, "/", "prefix must be a whole path segment");
  const win = [{ path: "C:\\", freeGB: 50 }, { path: "D:\\", freeGB: 800 }];
  assert.equal(d.pickDriveFor("D:\\Photos\\Lib", win).path, "D:\\");
  assert.equal(d.pickDriveFor("c:\\users\\x", win).path, "C:\\");
});

test("no GPU needed: CPU is the default and the only choice without a real accelerator", () => {
  const none = d.recommendMode({ accel: "cpu", name: null }, { ramGB: 8 });
  assert.equal(none.mode, "cpu");
  assert.deepEqual(none.modes, ["cpu"]);
  assert.doesNotMatch(none.note, /slow|GPU/i, "no-GPU copy must not sound like a failure");
  const igpu = d.recommendMode({ accel: "cpu", name: "Intel UHD 630", integrated: true }, { ramGB: 16 });
  assert.equal(igpu.mode, "cpu");
});

test("a real accelerator is offered, CPU stays selectable", () => {
  const nv = d.recommendMode({ accel: "cuda", vramGB: 8, name: "RTX 3070" }, { ramGB: 32 });
  assert.equal(nv.mode, "gpu");
  assert.deepEqual(nv.modes, ["gpu", "cpu"]);
  const small = d.recommendMode({ accel: "cuda", vramGB: 2, name: "GTX 950" }, { ramGB: 16 });
  assert.equal(small.mode, "cpu", "a 2 GB card is not worth offering");
});

test("storage math is plain words with round numbers", () => {
  assert.equal(d.storageMath(900), "900 GB free holds about 225,000 photos, or 20 hours of phone video.");
});

test("PIN rules", () => {
  assert.equal(d.validatePin("4827", "4827"), null);
  assert.match(d.validatePin("12"), /at least 4/);
  assert.match(d.validatePin("1111"), /less easy/);
  assert.match(d.validatePin("1234"), /less easy/);
  assert.match(d.validatePin("4827", "4828"), /don't match/);
});

test("backup must be on a different drive from the library", () => {
  assert.match(d.validateBackupTarget("", "/mnt/data/lib", drives), /Pick a drive/);
  assert.match(d.validateBackupTarget("/mnt/data/bk", "/mnt/data/lib", drives), /same drive/);
  assert.equal(d.validateBackupTarget("/mnt/elements/bk", "/mnt/data/lib", drives), null);
});

test("finish URLs: frame on http, private app on https, CA on http", () => {
  const u = d.finishUrls("192.0.2.10");
  assert.equal(u.wall, "http://192.0.2.10:8484/wall");
  assert.equal(u.app, "https://192.0.2.10:8485/menu");
  assert.equal(u.ca, "http://192.0.2.10:8484/ca.pem");
});

test("the backup drive can't also be a photo source", () => {
  assert.match(d.validateBackupTarget("/mnt/elements", "/mnt/data/lib", drives, ["/mnt/elements"]), /photo source/);
  assert.match(d.validateBackupTarget("/mnt/elements/bk", "/mnt/data/lib", drives, ["/mnt/elements"]), /photo source/);
  assert.equal(d.validateBackupTarget("/mnt/elements/bk", "/mnt/data/lib", drives, ["/mnt/data/pics"]), null);
});

test("removable drives are offered as sources but not pre-ticked", () => {
  assert.equal(d.defaultSourceChecked("/mnt/elements", drives), false);
  assert.equal(d.defaultSourceChecked("/mnt/data/Pictures", drives), true);
});
