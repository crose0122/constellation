"use strict";
// task #130 — the finish step must not claim startup it has not verified.
// finishClaims() is the pure truth table; serverUp() is the real probe whose
// result feeds it. The wizard renders ONLY what this says.
const test = require("node:test");
const assert = require("node:assert/strict");
const d = require("../decide");
const s = require("../setup");

test("finishClaims: autostart verified + server reachable = the honest all-good", () => {
  const c = d.finishClaims({ autostartOk: true, started: true, serverUp: true, background: true });
  assert.equal(c.ok, true);
  assert.equal(c.startsItself, true);
  assert.equal(c.running, true);
  assert.match(c.headline, /all set/i);
  assert.doesNotMatch(c.headline, /didn't start|not running/i);
});

test("finishClaims: no autostart, one-off launch up — running tonight, but it must NOT say it starts itself", () => {
  const c = d.finishClaims({ autostartOk: false, started: false, serverUp: true, background: true });
  assert.equal(c.ok, true, "the family still sees their sky tonight — not a failure");
  assert.equal(c.startsItself, false, "must not claim start-on-boot that was never installed");
  assert.match(c.bootWarning, /won't start by itself|every time/i);
  assert.match(c.headline, /all set/i);
});

test("finishClaims: autostart installed but startup NOT verified — say so, offer next step", () => {
  const c = d.finishClaims({ autostartOk: true, started: false, serverUp: false, background: true });
  assert.equal(c.startsItself, true, "the unit/task was installed and verified installable");
  assert.equal(c.running, false, "no verified startup means no claim of a running wall");
  assert.ok(c.retryHint, "the family needs a next step, not a shrug");
});

test("finishClaims: nothing running — the wizard must show a problem, never 'You're all set'", () => {
  const c = d.finishClaims({ autostartOk: false, started: false, serverUp: false, background: false });
  assert.equal(c.ok, false);
  assert.doesNotMatch(c.headline, /all set/i);
  assert.match(c.headline, /didn't start|not running/i);
  assert.ok(c.retryHint);
});

test("serverUp: true against a real local HTTP server, false when nothing listens", async () => {
  const http = require("http");
  const srv = http.createServer((req, res) => { res.statusCode = 200; res.end("ok"); });
  await new Promise((r) => srv.listen(0, "127.0.0.1", r));
  const port = srv.address().port;
  try {
    assert.equal(await s.serverUp("127.0.0.1", port, 2000), true, "a listening server is up");
  } finally { srv.close(); }
  // a port with nothing on it: bind, release, let the kernel settle, then probe
  const dead = await new Promise((r) => {
    const s2 = http.createServer();
    s2.listen(0, "127.0.0.1", () => { const p = s2.address().port; s2.close(() => r(p)); });
  });
  await new Promise((r) => setTimeout(r, 150));
  assert.equal(await s.serverUp("127.0.0.1", dead, 1500), false, "nothing listening = not up");
});