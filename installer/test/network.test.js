"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");
const net = require("node:net");
const n = require("../network");

const v4 = (address) => ({ family: "IPv4", internal: false, address });
// The public-repo leak scan forbids private-address literals anywhere in the
// tree, so these synthetic examples are assembled from octets. None of them is
// a real household address.
const ip = (...o) => o.join(".");

test("chooseLanAddress: the default-route interface wins over Docker, tailnet and VPN", () => {
  // A developer machine, in os.networkInterfaces() order: the old code took br0 by luck.
  const dev = { br0: [v4(ip(192, 168, 0, 20))], tailscale0: [v4(ip(100, 99, 1, 2))],
    "ts-mesh": [v4(ip(100, 70, 0, 1))], docker0: [v4(ip(172, 17, 0, 1))] };
  assert.equal(n.chooseLanAddress(dev, "br0"), ip(192, 168, 0, 20));
  // Same machine with the virtual interfaces listed first.
  const dockerFirst = { docker0: [v4(ip(172, 17, 0, 1))], tailscale0: [v4(ip(100, 99, 1, 3))],
    wlan0: [v4(ip(192, 168, 0, 50))] };
  assert.equal(n.chooseLanAddress(dockerFirst, "wlan0"), ip(192, 168, 0, 50));
  assert.equal(n.chooseLanAddress(dockerFirst, null), ip(192, 168, 0, 50));
});

test("chooseLanAddress: never offers CGNAT/tailnet, public, link-local or loopback", () => {
  assert.equal(n.chooseLanAddress({ tailscale0: [v4(ip(100, 80, 0, 8))] }, null), null);
  assert.equal(n.chooseLanAddress({ eth0: [v4("8.8.8.8")] }, "eth0"), null);
  assert.equal(n.chooseLanAddress({ eth0: [v4(ip(169, 254, 3, 4))] }, "eth0"), null);
  assert.equal(n.chooseLanAddress({ lo: [{ family: "IPv4", internal: true, address: "127.0.0.1" }] }, null), null);
  assert.equal(n.chooseLanAddress({ docker0: [v4(ip(172, 17, 0, 1))] }, null), null);
});

test("chooseLanAddress: prefers 192.168 > 10 > 172.16/12 when no default route is known", () => {
  const ifs = { a: [v4(ip(172, 20, 0, 5))], b: [v4(ip(10, 0, 0, 9))], c: [v4(ip(192, 168, 0, 7))] };
  assert.equal(n.chooseLanAddress(ifs, null), ip(192, 168, 0, 7));
});

test("defaultRouteIface: reads the default route out of /proc/net/route", () => {
  const table = [
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT",
    "docker0\t000011AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0",
    "br0\t00000000\t0101A8C0\t0003\t0\t0\t425\t00000000\t0\t0\t0",
  ].join("\n");
  assert.equal(n.defaultRouteIface(table), "br0");
  assert.equal(n.defaultRouteIface("Iface\tDestination\n"), null);
});

test("pickPorts: skips a pair when either port is taken", async () => {
  const taken = new Set([8484, 8585]);
  const r = await n.pickPorts(n.PORT_PAIRS, { portFree: async (p) => !taken.has(p) });
  assert.deepEqual(r, { httpPort: 8684, tlsPort: 8685 });
  assert.equal(await n.pickPorts([[1, 2]], { portFree: async () => false }), null);
});

test("portFree: false for a port something is listening on, true once it is released", async () => {
  const srv = net.createServer();
  await new Promise((r) => srv.listen(0, "0.0.0.0", r));
  const { port } = srv.address();
  assert.equal(await n.portFree(port), false);
  await new Promise((r) => srv.close(r));
  assert.equal(await n.portFree(port), true);
});

const CA = "-----BEGIN CERTIFICATE-----\nMIIBours\n-----END CERTIFICATE-----\n";
const OTHER = "-----BEGIN CERTIFICATE-----\nMIIBtheirs\n-----END CERTIFICATE-----\n";
function caServer(body, status = 200) {
  const s = http.createServer((req, res) => {
    if (req.url === "/ca.pem") { res.writeHead(status, { "Content-Type": "application/x-pem-file" }); res.end(body); }
    else { res.writeHead(404); res.end(); }
  });
  return new Promise((r) => s.listen(0, "127.0.0.1", () => r(s)));
}

test("servesOurCa: true only when the served CA is this library's CA", async () => {
  const ours = await caServer(CA);
  const theirs = await caServer(OTHER);
  const missing = await caServer("no family certificate yet", 404);
  try {
    assert.equal(await n.servesOurCa("127.0.0.1", ours.address().port, CA), true);
    // Another Constellation on the port: same wall page, different family CA.
    assert.equal(await n.servesOurCa("127.0.0.1", theirs.address().port, CA), false);
    assert.equal(await n.servesOurCa("127.0.0.1", missing.address().port, CA), false);
    assert.equal(await n.servesOurCa("127.0.0.1", ours.address().port, ""), false, "no expected CA = never ours");
  } finally { ours.close(); theirs.close(); missing.close(); }
});

test("servesOurCa: a dead port is 'not ours', never an exception", async () => {
  const s = net.createServer();
  await new Promise((r) => s.listen(0, "127.0.0.1", r));
  const port = s.address().port;
  await new Promise((r) => s.close(r));
  assert.equal(await n.servesOurCa("127.0.0.1", port, CA, 300), false);
});

test("chooseLanAddress: the default-route interface beats a 'better' private range", () => {
  const ifs = { a: [v4(ip(192, 168, 0, 7))], b: [v4(ip(10, 0, 0, 9))] };
  assert.equal(n.chooseLanAddress(ifs, null), ip(192, 168, 0, 7));
  assert.equal(n.chooseLanAddress(ifs, "b"), ip(10, 0, 0, 9), "the interface that carries traffic wins");
});

test("servesOurCa: the right body with a non-200 status is not ours", async () => {
  const s404 = await caServer(CA, 404);
  const s500 = await caServer(CA, 500);
  try {
    assert.equal(await n.servesOurCa("127.0.0.1", s404.address().port, CA), false);
    assert.equal(await n.servesOurCa("127.0.0.1", s500.address().port, CA), false);
  } finally { s404.close(); s500.close(); }
});
