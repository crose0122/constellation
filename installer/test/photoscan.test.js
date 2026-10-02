"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const ps = require("../photoscan");

const PHOTO = Buffer.alloc(60 * 1024, 7);     // big enough to count as a photo
const ICON = Buffer.alloc(2 * 1024, 7);       // an icon: never counted

function put(root, rel, buf = PHOTO, year = 2019) {
  const f = path.join(root, rel);
  fs.mkdirSync(path.dirname(f), { recursive: true });
  fs.writeFileSync(f, buf);
  const t = new Date(`${year}-06-01T12:00:00Z`);
  fs.utimesSync(f, t, t);
  return f;
}

function tree() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "photoscan-"));
  const home = path.join(tmp, "home");
  const drive = path.join(tmp, "USB");
  for (let i = 0; i < 5; i++) put(home, `Pictures/Camera/IMG_${i}.jpg`, PHOTO, 2014 + i);
  for (let i = 0; i < 25; i++) put(drive, `Family 2019/trip/DSC${i}.JPG`, PHOTO, 2019);
  put(drive, "Family 2019/trip/clip.mp4", PHOTO, 2019);
  for (let i = 0; i < 40; i++) put(home, `code/app/assets/icon${i}.png`, PHOTO);  // a software project…
  put(home, "code/app/package.json", Buffer.from("{}"));                          // …says so
  for (let i = 0; i < 40; i++) put(home, `Downloads/ui/icon${i}.png`, ICON);       // tiny: not photos
  for (let i = 0; i < 30; i++) put(home, `.cache/thumbs/t${i}.jpg`, PHOTO);        // hidden: skipped
  for (let i = 0; i < 30; i++) put(home, `Constellation/library/originals/o${i}.jpg`, PHOTO); // our own library
  return { tmp, home, drive };
}

test("scan finds family photo places, with counts, years and samples", async () => {
  const { tmp, home, drive } = tree();
  try {
    const r = await ps.scanForPhotos({ roots: [{ path: home, label: "Home" }, { path: drive, label: "USB" }],
      exclude: [path.join(home, "Constellation")] });
    const byName = Object.fromEntries(r.places.map((p) => [p.name, p]));
    assert.deepEqual(Object.keys(byName).sort(), ["Family 2019", "Pictures"]);
    assert.equal(byName.Pictures.photos, 5, "well-known folder listed below the minimum");
    assert.equal(byName.Pictures.wellKnown, true);
    assert.equal(byName.Pictures.firstYear, 2014);
    assert.equal(byName.Pictures.lastYear, 2018);
    assert.equal(byName["Family 2019"].photos, 25);
    assert.equal(byName["Family 2019"].videos, 1);
    assert.equal(byName["Family 2019"].rootLabel, "USB");
    assert.equal(byName["Family 2019"].samples.length, 4);
    assert.equal(r.places[0].name, "Pictures", "well-known photo folders come first");
    assert.equal(r.projects >= 1, true, "the software project was recognised and skipped");
  } finally { fs.rmSync(tmp, { recursive: true, force: true }); }
});

test("scan never reads excluded folders (the library itself, the backup drive)", async () => {
  const { tmp, home, drive } = tree();
  try {
    const r = await ps.scanForPhotos({ roots: [{ path: home, label: "Home" }, { path: drive, label: "USB" }],
      exclude: [path.join(home, "Constellation"), drive] });
    assert.equal(r.places.some((p) => p.path.startsWith(drive)), false);
    assert.equal(r.places.some((p) => p.path.includes("Constellation")), false);
    assert.ok(r.skipped.includes("USB"));
  } finally { fs.rmSync(tmp, { recursive: true, force: true }); }
});

test("scan does not count the same folder twice through a second path", async () => {
  const { tmp, home } = tree();
  try {
    const data = path.join(tmp, "data");
    fs.mkdirSync(data);
    fs.symlinkSync(home, path.join(data, "home"));     // stands in for a second path to home
    const r = await ps.scanForPhotos({ roots: [{ path: home, label: "Home" }, { path: data, label: "data" }],
      exclude: [path.join(home, "Constellation")] });
    assert.equal(r.places.filter((p) => p.name === "Pictures").length, 1);
    assert.equal(r.places.some((p) => p.rootLabel === "data"), false);
  } finally { fs.rmSync(tmp, { recursive: true, force: true }); }
});

test("scan stops at its budget and says it was partial", async () => {
  const { tmp, home } = tree();
  try {
    let t = 0;
    const r = await ps.scanForPhotos({ roots: [{ path: home, label: "Home" }], budgetMs: 5, now: () => (t += 10) });
    assert.equal(r.partial, true);
  } finally { fs.rmSync(tmp, { recursive: true, force: true }); }
});

test("summarizeFolder describes exactly the folder that was picked", async () => {
  const { tmp, drive } = tree();
  try {
    const r = await ps.summarizeFolder(path.join(drive, "Family 2019"));
    assert.equal(r.place.photos, 25);
    assert.equal(r.place.name, "Family 2019");
    assert.equal(r.place.samples.length, 12);
  } finally { fs.rmSync(tmp, { recursive: true, force: true }); }
});

test("scanRoots: home plus data drives, never system mounts or the drive holding home", () => {
  const drives = [{ path: "/" }, { path: "/boot/efi" }, { path: "/home" }, { path: "/snap/core/1" },
    { path: "/run/media/kim/USB", label: "USB", removable: true }, { path: "/data", label: "data" },
    { path: "/run/user/1000" }];
  const r = ps.scanRoots("/home/example", drives, "linux").map((x) => x.path);
  assert.deepEqual(r, ["/home/example", "/run/media/kim/USB", "/data"]);
  const w = ps.scanRoots("C:\\Users\\Kim", [{ path: "D:\\", label: "Photos", type: "fixed" },
    { path: "Z:\\", type: "network" }], "win32").map((x) => x.label);
  assert.deepEqual(w, ["Home", "Photos (D:)"]);
});
