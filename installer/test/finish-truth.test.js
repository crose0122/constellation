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
    { status: 302, type: "text/html", body, location: "/wall" },
    { status: 404, type: "text/html", body },
    { status: 200, type: "text/html", body: "unrelated web server" },
    { status: 200, type: "application/json", body },
    { status: 200, type: "text/html-not-really", body },
  ]) {
    const other = http.createServer((_req, res) => {
      res.statusCode = response.status;
      res.setHeader("content-type", response.type);
      if (response.location) res.setHeader("location", response.location);
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

test("serverUp: parses Content-Type as one exact text/html media type", () => {
  for (const value of [
    "text/html",
    "TEXT/HTML",
    " \ttext/html\t ",
    "text/html; charset=utf-8",
    "TEXT/HTML ; charset=\"utf-8\"; boundary=safe",
  ]) assert.equal(s.isHtmlMediaType(value), true, value);

  for (const value of [
    "text/html-not-really",
    "text/html, application/json",
    "text/html; charset",
    "text/html; =utf-8",
    "text/html; charset=\"unterminated",
    "text /html",
    ["text/html", "application/json"],
    "application/xhtml+xml",
    "",
  ]) assert.equal(s.isHtmlMediaType(value), false, String(value));
});

test("serverUp: one deadline bounds connect, headers, and the complete response body", async () => {
  const http = require("http");
  const cases = [
    (_req, _res) => {},
    (_req, res) => {
      res.writeHead(200, { "content-type": "text/html" });
      res.write("<title>Constellation — the wall</title>");
      const drip = setInterval(() => res.write("."), 10);
      res.on("close", () => clearInterval(drip));
    },
  ];
  for (const handler of cases) {
    const server = http.createServer(handler);
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const started = Date.now();
    try {
      assert.equal(await s.serverUp("127.0.0.1", server.address().port, 60), false);
      assert.ok(Date.now() - started < 500, "the caller's deadline must replace the request's fixed timeout");
    } finally {
      if (server.closeAllConnections) server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    }
  }
});

test("serverUp: rejects truncated, errored, and oversized bodies and closes their sockets", async () => {
  const http = require("http");
  const handlers = [
    (_req, res) => {
      res.writeHead(200, { "content-type": "text/html", "content-length": "999" });
      res.write("<title>Constellation — the wall</title>");
      res.socket.destroy();
    },
    (_req, res) => {
      res.writeHead(200, { "content-type": "text/html" });
      res.end("x".repeat(65537) + "<title>Constellation — the wall</title>");
    },
  ];
  for (const handler of handlers) {
    const sockets = new Set();
    const server = http.createServer(handler);
    server.on("connection", (socket) => {
      sockets.add(socket);
      socket.on("close", () => sockets.delete(socket));
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    try {
      assert.equal(await s.serverUp("127.0.0.1", server.address().port, 80), false);
      await new Promise((resolve) => setTimeout(resolve, 20));
      assert.equal(sockets.size, 0, "failed attempts must not leave a response socket open");
    } finally {
      if (server.closeAllConnections) server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    }
  }
});

test("serverUp: settles once and removes request and response listeners", async () => {
  const { EventEmitter } = require("events");
  const request = new EventEmitter();
  request.destroyed = false;
  request.destroy = () => { request.destroyed = true; queueMicrotask(() => request.emit("close")); };
  let response;
  let calls = 0;
  const http = {
    get(_options, callback) {
      calls += 1;
      queueMicrotask(() => {
        response = new EventEmitter();
        response.statusCode = 200;
        response.headers = { "content-type": "text/html" };
        response.destroyed = false;
        response.setEncoding = () => {};
        response.destroy = () => { response.destroyed = true; queueMicrotask(() => response.emit("close")); };
        callback(response);
        response.emit("data", "<title>Constellation — the wall</title>");
        response.emit("end");
        response.emit("aborted");
      });
      return request;
    },
  };
  assert.equal(await s.serverUp("ignored", 1, 100, { http }), true);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls, 1);
  assert.equal(request.destroyed, true);
  assert.equal(response.destroyed, true);
  assert.equal(request.eventNames().length, 0);
  assert.equal(response.eventNames().length, 0);
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
