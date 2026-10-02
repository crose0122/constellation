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

// 2026-10-02 regression (a hands-on test run): an older Constellation already
// held :8484, its wall passed the readiness probe, and "Open the sky" showed a
// different family's library. A wall page is not proof — the CA must be ours.
function identityHarness({ ours, cfg = {} }) {
  const seen = {};
  const deps = {
    writeConfig: () => "/data/.env",
    backendExe: () => "/app/brain",
    installAutostart: async (a) => { seen.autostart = a; return { ok: true, started: true,
      startsOnLogin: true, startsOnBoot: true, rollback: async () => { seen.rolledBack = true; return { ok: true }; } }; },
    launchStack: () => {},
    serverUp: async (_h, port) => { seen.probedPort = port; return true; },
    servesOurCa: async (_h, port, pem) => { seen.caPort = port; seen.pem = pem; return ours; },
    readOurCa: () => "-----BEGIN CERTIFICATE-----\nours\n-----END CERTIFICATE-----\n",
    startBackgroundSweep: () => ({ ok: true, verified: false }),
    finishClaims: require("../decide").finishClaims,
  };
  return { seen, run: () => finishInstall({ cfg, dataDir: "/data", backendDir: "/b", appDir: "/a",
    send: () => {}, deps }) };
}

test("finish flow refuses a server that answers the wall but serves someone else's CA", async () => {
  const h = identityHarness({ ours: false, cfg: { httpPort: 8584, tlsPort: 8585 } });
  const r = await h.run();
  assert.equal(r.ok, false);
  assert.equal(r.serverUp, false);
  assert.match(r.error, /Something else is answering on port 8584/);
  assert.equal(h.seen.rolledBack, true, "autostart for a server that isn't ours is rolled back");
});

test("finish flow uses the picked ports everywhere and reports them", async () => {
  const h = identityHarness({ ours: true, cfg: { httpPort: 8684, tlsPort: 8685 } });
  const r = await h.run();
  assert.equal(r.ok, true);
  assert.equal(h.seen.probedPort, 8684);
  assert.equal(h.seen.caPort, 8684);
  assert.equal(h.seen.autostart.httpPort, 8684);
  assert.equal(h.seen.autostart.tlsPort, 8685);
  assert.equal(r.httpPort, 8684);
  assert.equal(r.tlsPort, 8685);
  assert.match(h.seen.pem, /BEGIN CERTIFICATE/);
});
