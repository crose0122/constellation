#!/usr/bin/env node
// Generate / verify the installer's shipped-but-not-committed assets (task #137).
//
//   gen-shipped-assets.js            generate pack-time assets that are missing
//   gen-shipped-assets.js --check    verify every manifest stage is satisfied:
//                                    commit-time entries must exist NOW (used
//                                    by `npm test` via pretest); pack-time
//                                    entries are only required when packaging,
//                                    and `npm run dist` runs this script first
//                                    so electron-builder never ships a partial
//                                    installer.
//
// Manifest: installer/assets/generated-files.json — {path, stage} entries.
"use strict";
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const installerDir = path.resolve(__dirname, "..", "installer");
const CHECK = process.argv.includes("--check");

const manifestPath = path.join(installerDir, "assets", "generated-files.json");
const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));

const missing = manifest.filter((m) => !fs.existsSync(path.resolve(installerDir, m.path)));

function fail(msg) {
  console.error(msg);
  process.exit(1);
}

if (CHECK) {
  const commitTimeMissing = missing.filter((m) => m.stage === "commit-time");
  if (commitTimeMissing.length) {
    fail("commit-time shipped files missing (restore from git):\n  " +
         commitTimeMissing.map((m) => m.path).join("\n  "));
  }
  console.log("asset manifest OK (" +
              (manifest.length - missing.length) + "/" + manifest.length +
              " present; pack-time built by `npm run gen:assets` before dist)");
  process.exit(0);
}

// Generate mode: build pack-time assets that are missing.
const cannotGenerate = [];
for (const m of missing) {
  if (m.stage !== "pack-time") {
    cannotGenerate.push(`${m.path} (stage ${m.stage} — restore from git, not generated)`);
    continue;
  }
  if (m.path === "../scripts/dist/memoryvault-brain") {
    try {
      execFileSync("bash", [path.resolve(__dirname, "build-backend.sh")],
                   { stdio: "inherit", timeout: 600000 });
    } catch {
      cannotGenerate.push("../scripts/dist/memoryvault-brain (build-backend.sh failed)");
    }
  } else if (m.path === "models/nsfw-screen.onnx") {
    try {
      execFileSync("bash", [path.resolve(installerDir, "fetch-models.sh")],
                   { stdio: "inherit", timeout: 600000 });
    } catch {
      cannotGenerate.push("models/nsfw-screen.onnx (fetch-models.sh failed)");
    }
  } else {
    cannotGenerate.push(`${m.path} (no generator wired for this path)`);
  }
}

if (cannotGenerate.length) fail("cannot generate:\n  " + cannotGenerate.join("\n  "));
if (!missing.length) console.log("nothing to generate — all manifest entries exist");