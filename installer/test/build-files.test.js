"use strict";
// Every local module the main process can reach must ship in the packaged
// app. A module missing from build.files passes every unit test (they load it
// from the source tree) and then crashes the installed app at launch.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.join(__dirname, "..");
const pkg = require("../package.json");

test("every local module reachable from main.js is in build.files", () => {
  const seen = new Set();
  const todo = ["main.js"];
  while (todo.length) {
    const f = todo.pop();
    if (seen.has(f)) continue;
    seen.add(f);
    const src = fs.readFileSync(path.join(ROOT, f), "utf8");
    for (const m of src.matchAll(/require\("\.\/([^"]+)"\)/g)) {
      todo.push(m[1].endsWith(".js") ? m[1] : `${m[1]}.js`);
    }
  }
  const missing = [...seen].filter((f) => !pkg.build.files.includes(f));
  assert.deepEqual(missing, [], `not shipped: ${missing.join(", ")}`);
});
