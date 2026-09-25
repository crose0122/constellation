"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const d = require("../decide");
const s = require("../setup");

test("finishClaims: autostart verified + server reachable = the honest all-good", () => {
  const c = d.finishClaims({ autostartOk: true, startsOnBoot: true, started: true, serverUp: true, background: true });
  assert.equal(c.ok, true);
  assert.equal(c.startsItself, true);
  assert.equal(c.running, true);
  assert.match(c.headline, /all set/i);
});

test("finishClaims: no autostart, one-off launch up is only running for now", () => {
  const c = d.finishClaims({ autostartOk: false, started: false, serverUp: true, background: true });
  assert.equal(c.ok, true);
  assert.equal(c.startsItself, false);
  assert.match(c.bootWarning, /won't start by itself|every time/i);
});

test("finishClaims: task start is not product readiness", () => {
  const c = d.finishClaims({ autostartOk: true, startsOnBoot: true, started: true, serverUp: false, background: true });
  assert.equal(c.ok, false, "schtasks/systemctl success cannot substitute for the product probe");
  assert.equal(c.running, false);
  assert.ok(c.retryHint);
});

test("finishClaims: login startup and boot startup are distinct claims", () => {
  const loginOnly = d.finishClaims({ autostartOk: true, startsOnLogin: true,
    startsOnBoot: false, started: true, serverUp: true, background: false });
  assert.equal(loginOnly.startsItself, false, "login-only startup must not earn the boot/startsItself claim");
  assert.equal(loginOnly.startsOnLogin, true);
  assert.equal(loginOnly.startsOnBoot, false);
  assert.match(loginOnly.startupNote, /signs? in|log(?:s| )?in/i);
  assert.doesNotMatch(loginOnly.startupNote, /turns on|boot/i);
});

test("finishClaims: nothing running is never all set", () => {
  const c = d.finishClaims({ autostartOk: false, started: false, serverUp: false, background: false });
  assert.equal(c.ok, false);
  assert.doesNotMatch(c.headline, /all set/i);
  assert.ok(c.retryHint);
});

test("serverUp: requires the exact Constellation wall response, not merely an HTTP listener", async () => {
  const http = require("http");
  const body = "<!doctype html><title>Constellation — the wall</title>";
  const good = http.createServer((_req, res) => {
    res.statusCode = 200;
    res.setHeader("content-type", "text/html; charset=utf-8");
    res.end(body);
  });
  await new Promise((r) => good.listen(0, "127.0.0.1", r));
  try { assert.equal(await s.serverUp("127.0.0.1", good.address().port, 2000), true); }
  finally { await new Promise((r) => good.close(r)); }

  for (const response of [
    { status: 404, type: "text/html", body },
    { status: 200, type: "text/html", body: "unrelated web server" },
    { status: 200, type: "application/json", body },
  ]) {
    const other = http.createServer((_req, res) => {
      res.statusCode = response.status;
      res.setHeader("content-type", response.type);
      res.end(response.body);
    });
    await new Promise((r) => other.listen(0, "127.0.0.1", r));
    try {
      assert.equal(await s.serverUp("127.0.0.1", other.address().port, 50), false,
        `${response.status} ${response.type} is not readiness`);
    } finally { await new Promise((r) => other.close(r)); }
  }

  const dead = await new Promise((r) => {
    const srv = http.createServer();
    srv.listen(0, "127.0.0.1", () => { const p = srv.address().port; srv.close(() => r(p)); });
  });
  assert.equal(await s.serverUp("127.0.0.1", dead, 50), false);
});

test("finish copy promises overnight only for verified background capability", () => {
  const verified = d.finishCopy({ startsOnBoot: true, startsOnLogin: true, background: true, backgroundStarted: true });
  assert.match(verified.startup, /turns on/i);
  assert.match(verified.progress, /overnight|on their own/i);

  const spawned = d.finishCopy({ startsOnBoot: false, startsOnLogin: true, background: false, backgroundStarted: true });
  assert.match(spawned.startup, /signs? in/i);
  assert.doesNotMatch(spawned.startup, /turns on/i);
  assert.doesNotMatch(spawned.progress, /overnight|on their own/i);
  assert.match(spawned.progress, /keep.*on|signed in|progress/i);
});
