"use strict";
// Where the family's Constellation answers, and proof that it is theirs.
//
// Three jobs, all kept outside Electron so they can be tested directly:
//   - pick a free HTTP/TLS port pair, so an existing service (another
//     Constellation, a dev server, anything) can never be mistaken for ours;
//   - choose the address phones and TVs should use: the private LAN address
//     on the default route, never Docker, VPN or other virtual interfaces;
//   - prove the server that answers is THIS install's: the family CA it
//     serves at /ca.pem must match the one the wizard just created in the
//     library. The wall page alone proves nothing — every Constellation
//     serves the same one.
const fs = require("fs");
const net = require("net");
const os = require("os");

const PORT_PAIRS = [[8484, 8485], [8584, 8585], [8684, 8685], [8784, 8785], [8884, 8885]];

function portFree(port, host = "0.0.0.0", runtime = {}) {
  const mk = runtime.createServer || net.createServer;
  return new Promise((resolve) => {
    const srv = mk();
    srv.once("error", () => resolve(false));
    srv.once("listening", () => srv.close(() => resolve(true)));
    try { srv.listen(port, host); } catch { resolve(false); }
  });
}

// The first pair whose both ports are free on every interface, or null.
async function pickPorts(pairs = PORT_PAIRS, runtime = {}) {
  const free = runtime.portFree || portFree;
  for (const [http, tls] of pairs) {
    if (await free(http) && await free(tls)) return { httpPort: http, tlsPort: tls };
  }
  return null;
}

// Interfaces that are never "this computer on the home network".
const VIRTUAL_IFACE = /^(lo|docker|br-|veth|virbr|vmnet|vboxnet|tailscale|ts-|tun|tap|wg|zt|utun|ham|cni|flannel|kube|lxc|lxd)|vethernet|virtualbox|vmware|hyper-v|loopback|bluetooth/i;

function privateRank(addr) {
  const [a, b] = addr.split(".").map(Number);
  if (a === 192 && b === 168) return 1;
  if (a === 10) return 2;
  if (a === 172 && b >= 16 && b <= 31) return 3;
  return 0;                                    // public, CGNAT/tailnet, link-local: never
}

// Linux: the interface that carries the default route, from /proc/net/route.
function defaultRouteIface(routeTable) {
  if (typeof routeTable !== "string") {
    try { routeTable = fs.readFileSync("/proc/net/route", "utf8"); } catch { return null; }
  }
  for (const line of routeTable.split("\n").slice(1)) {
    const f = line.trim().split(/\s+/);
    if (f.length > 7 && f[1] === "00000000" && f[7] === "00000000") return f[0];
  }
  return null;
}

// Pure: pick the address phones and TVs should use, or null.
function chooseLanAddress(ifaces, defaultIface = null) {
  const cands = [];
  for (const [name, addrs] of Object.entries(ifaces || {})) {
    for (const a of addrs || []) {
      if (a.family !== "IPv4" && a.family !== 4) continue;
      if (a.internal) continue;
      const rank = privateRank(a.address);
      if (!rank) continue;
      const virtual = VIRTUAL_IFACE.test(name);
      if (virtual && name !== defaultIface) continue;
      cands.push({ address: a.address, rank, onDefault: name === defaultIface });
    }
  }
  cands.sort((x, y) => (y.onDefault - x.onDefault) || (x.rank - y.rank));
  return cands.length ? cands[0].address : null;
}

function lanAddress(runtime = {}) {
  const ifaces = runtime.ifaces || os.networkInterfaces();
  const dflt = process.platform === "linux" ? defaultRouteIface(runtime.routeTable) : null;
  return chooseLanAddress(ifaces, dflt);
}

// GET http://host:port/ca.pem and compare it with the library's own CA.
// Bounded, never throws: any failure is "not ours".
function servesOurCa(host, port, expectedPem, timeoutMs = 4000, runtime = {}) {
  const http = runtime.http || require("http");
  const want = Buffer.isBuffer(expectedPem) ? expectedPem.toString("utf8") : String(expectedPem || "");
  if (!want.includes("BEGIN CERTIFICATE")) return Promise.resolve(false);
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => { if (!done) { done = true; clearTimeout(timer); resolve(v); } };
    const timer = setTimeout(() => { req.destroy(); finish(false); }, timeoutMs);
    const req = http.get({ host, port, path: "/ca.pem", timeout: timeoutMs }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (c) => { body += c; if (body.length > 65536) { req.destroy(); finish(false); } });
      res.on("end", () => finish(res.statusCode === 200 && body.trim() === want.trim()));
      res.on("error", () => finish(false));
    });
    req.on("error", () => finish(false));
    req.on("timeout", () => { req.destroy(); finish(false); });
  });
}

module.exports = { PORT_PAIRS, portFree, pickPorts, chooseLanAddress, defaultRouteIface,
  lanAddress, servesOurCa, privateRank };
