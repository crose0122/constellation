"use strict";
// Regression for task #137: `npm audit fix --package-lock-only` must be a
// no-op on the committed lockfile. Any advisory that npm can clear inside the
// current semver ranges is thereby guaranteed fixed and stays fixed.
// Advisories that need a MAJOR bump of a direct dependency (electron,
// electron-builder) are a tracked README item, not silently tolerated.
//
// RED evidence (2026-09-25): @xmldom/xmldom 0.9.10 (3 high) and js-yaml 4.3.1
// (1 high) sat in the shipped lockfile with in-range fixes available.
// NOTE: npm's fixAvailable:true can be a false positive when a transitive
// dep is exactly-pinned by its parent (electron-publish 25.1.7 is pinned by
// app-builder-lib 25.1.8) — which is exactly why this test compares what
// audit fix WOULD DO instead of trusting the flag.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const os = require("os");

const installerDir = path.resolve(__dirname, "..");

test("npm audit fix --package-lock-only is a no-op on the committed lockfile", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "lock-audit-"));
  try {
    for (const f of ["package.json", "package-lock.json"]) {
      fs.copyFileSync(path.join(installerDir, f), path.join(tmp, f));
    }
    const opts = { cwd: tmp, encoding: "utf8", timeout: 180000 };
    try {
      execFileSync("npm", ["audit", "fix", "--package-lock-only"], opts);
    } catch {
      /* audit fix exits non-zero when advisories remain — expected */
    }
    const before = fs.readFileSync(path.join(tmp, "package-lock.json"), "utf8");
    const after = fs.readFileSync(path.join(installerDir, "package-lock.json"), "utf8");
    assert.equal(after, before,
      "npm audit fix would change the lockfile — run " +
      "`npm audit fix --package-lock-only` and commit the result");
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});