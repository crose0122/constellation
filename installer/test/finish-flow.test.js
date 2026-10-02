"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { finishInstall } = require("../finish-install");

function harness(overrides = {}) {
  const calls = [];
  const auto = overrides.auto || { ok: true, started: true, startsOnLogin: true,
    startsOnBoot: true, rollback: async () => { calls.push("rollback"); return { ok: true }; } };
  const deps = {
    writeConfig: () => "/data/.env",
    backendExe: () => "/app/brain",
    installAutostart: async () => auto,
    launchStack: () => { calls.push("launch"); },
    serverUp: async () => overrides.serverUp !== false,
    startBackgroundSweep: () => ({ ok: true, verified: false }),
    finishClaims: require("../decide").finishClaims,
  };
  return { calls, auto, run: () => finishInstall({ cfg: {}, dataDir: "/data",
    backendDir: "/backend", appDir: "/app", send: () => {}, deps }) };
}

test("finish flow probes Linux success because install reports started", async () => {
  const h = harness();
  const r = await h.run();
  assert.equal(r.ok, true);
  assert.equal(r.serverUp, true);
});

test("finish flow rolls autostart back when product readiness fails", async () => {
  const h = harness({ serverUp: false });
  const r = await h.run();
  assert.equal(r.ok, false);
  assert.deepEqual(h.calls, ["rollback"]);
  assert.equal(r.autostart, false);
});

test("finish flow reports rollback cleanup failure without hiding readiness failure", async () => {
  const auto = { ok: true, started: true, startsOnLogin: true, startsOnBoot: true,
    rollback: async () => ({ ok: false, error: "delete task failed; restore launcher failed" }) };
  const h = harness({ auto, serverUp: false });
  const r = await h.run();
  assert.equal(r.ok, false);
  assert.match(r.error, /readiness/i);
  assert.match(r.rollbackError, /delete task failed/);
  assert.match(r.rollbackError, /restore launcher failed/);
});

test("finish flow rolls back when the readiness probe throws", async () => {
  const h = harness();
  h.run = () => finishInstall({ cfg: {}, dataDir: "/data", backendDir: "/backend", appDir: "/app",
    send: () => {}, deps: {
      writeConfig: () => "/data/.env", backendExe: () => "/app/brain",
      installAutostart: async () => h.auto, launchStack: () => {},
      serverUp: async () => { throw new Error("probe timeout"); },
      startBackgroundSweep: () => ({ ok: true, verified: false }),
      finishClaims: require("../decide").finishClaims,
    } });
  const r = await h.run();
  assert.equal(r.ok, false);
  assert.deepEqual(h.calls, ["rollback"]);
  assert.match(r.error, /probe timeout/i);
});

test("detached spawn alone does not verify overnight completion", async () => {
  const h = harness();
  const r = await h.run();
  assert.equal(r.background, false);
  assert.equal(r.backgroundStarted, true);
});
