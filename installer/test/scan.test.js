"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { parseDfLsblk } = require("../scan");

// Synthetic fixture: a USB backup drive under /run/media, internal SATA also
// auto-mounted under /run/media, and tmpfs noise.
const DF = `Filesystem 1024-blocks Used Available Capacity Mounted on
tmpfs 6567112 3196 6563916 1% /run
/dev/nvme0n1p2 98000000 80000000 15000000 85% /
tmpfs 24000000 1000 24000000 1% /tmp
/dev/nvme1n1p1 1900000000 400000000 1432000000 22% /mnt/fastdisk
/dev/sda1 1900000000 300000000 1539000000 17% /mnt/library
/dev/sdb2 400000000 130000000 268000000 33% /run/media/test-user/SYNTHETIC_INTERNAL
/dev/sdc1 2000000000 500000000 1500000000 25% /run/media/test-user/SYNTHETIC_USB
/dev/nvme0n1p1 1000000 6000 994000 1% /boot/efi
/dev/loop0 128 128 0 100% /snap/bare/5
overlay 50000000 1 50000000 1% /var/lib/lemonade`;
const LSBLK = JSON.stringify({ blockdevices: [
  { name: "sdc", hotplug: true, rm: false, tran: "usb", mountpoints: [null],
    children: [{ name: "sdc1", hotplug: false, rm: false, tran: null, label: "SYNTHETIC_USB", mountpoints: ["/run/media/test-user/SYNTHETIC_USB"] }] },
  { name: "sdb", hotplug: false, rm: false, tran: "sata", mountpoints: [null],
    children: [{ name: "sdb2", hotplug: false, rm: false, tran: null, label: "SYNTHETIC_INTERNAL", mountpoints: ["/run/media/test-user/SYNTHETIC_INTERNAL"] }] },
  { name: "sda", hotplug: false, rm: false, tran: "sata", mountpoints: [null],
    children: [{ name: "sda1", hotplug: false, label: null, mountpoints: ["/mnt/library"] }] },
] });

const drives = parseDfLsblk(DF, LSBLK);
const by = Object.fromEntries(drives.map((d) => [d.path, d]));

test("a USB drive under /run/media is found and marked removable (the backup target)", () => {
  assert.ok(by["/run/media/test-user/SYNTHETIC_USB"], "USB media must not be filtered out with /run");
  assert.equal(by["/run/media/test-user/SYNTHETIC_USB"].removable, true);
  assert.equal(by["/run/media/test-user/SYNTHETIC_USB"].label, "SYNTHETIC_USB");
  assert.equal(by["/run/media/test-user/SYNTHETIC_USB"].freeGB, 1500);
});

test("an internal disk auto-mounted under /run/media is NOT called removable", () => {
  assert.equal(by["/run/media/test-user/SYNTHETIC_INTERNAL"].removable, false);
});

test("pseudo and scratch mounts are not offered", () => {
  for (const m of ["/run", "/tmp", "/boot/efi", "/snap/bare/5", "/var/lib/lemonade"])
    assert.equal(by[m], undefined, m);
});

test("real disks are listed with free space", () => {
  assert.equal(by["/mnt/library"].freeGB, 1539);
  assert.equal(by["/"].freeGB, 15);
});

test("without lsblk, /run/media still counts as removable (best effort)", () => {
  const d = parseDfLsblk(DF, "");
  assert.equal(d.find((x) => x.path === "/run/media/test-user/SYNTHETIC_USB").removable, true);
});
