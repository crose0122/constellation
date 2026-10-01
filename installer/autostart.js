// Constellation Setup — start on boot (V2 CP2, spec A2).
// Linux: a systemd *user* unit (no root) + linger so it runs without a login.
// Windows: a per-user Scheduled Task at logon (no service account, no admin).
// The unit/task text is built by pure functions so tests can pin it exactly.
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFile } = require("child_process");

const UNIT = "constellation.service";
// Written as the first line of every unit this installer creates. A unit
// without it was made by hand (or by an older setup) and is never overwritten:
// that could silently replace a working install's settings.
const MARKER = "# managed-by: constellation-setup";

function mayWriteUnit(existingText) {
  return existingText == null || String(existingText).startsWith(MARKER);
}
const TASK = "Constellation";

function sh(cmd, args, timeout = 20000) {
  return new Promise((resolve) => execFile(cmd, args, { timeout, windowsHide: true },
    (err, out, errOut) => resolve({ ok: !err, out: String(out || ""), err: String(errOut || (err && err.message) || "") })));
}

// systemd quoting: one argument per word, escape backslash/quote, wrap in "".
function sdQuote(s) { return `"${String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`; }

function systemdUnit({ exe, envFile, httpPort = 8484, tlsPort = 8485 }) {
  if (!path.isAbsolute(exe)) throw new Error("backend path must be absolute");
  if (!path.isAbsolute(envFile)) throw new Error("config path must be absolute");
  return [
    MARKER,
    "[Unit]",
    "Description=Constellation — family photos, safe at home",
    "After=network-online.target",
    "Wants=network-online.target",
    "",
    "[Service]",
    "Type=simple",
    `EnvironmentFile=${envFile}`,
    `ExecStart=${sdQuote(exe)} constellation --host 0.0.0.0 --port ${httpPort} --tls-port ${tlsPort}`,
    "Restart=always",
    "RestartSec=5",
    // a picture frame must come back no matter how many times it falls over
    "StartLimitIntervalSec=0",
    "NoNewPrivileges=yes",
    "PrivateTmp=yes",
    "",
    "[Install]",
    "WantedBy=default.target",
    "",
  ].join("\n");
}

// Windows Task Scheduler XML. Runs as the installing user at logon, restarts
// on failure, no time limit, hidden. The config is passed via a wrapper .cmd so
// the XML never embeds secrets or long env strings.
function xmlEsc(s) { return String(s).replace(/[<>&"']/g, (c) =>
  ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&apos;" }[c])); }

function windowsTaskXml({ launcher, user }) {
  return `<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Constellation — family photos, safe at home</Description></RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>true</Enabled><UserId>${xmlEsc(user)}</UserId></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>${xmlEsc(user)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
    <Hidden>true</Hidden>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>${xmlEsc(launcher)}</Command></Exec></Actions>
</Task>
`;
}

// .env → `set KEY=VALUE` lines. Values with cmd metacharacters are refused
// rather than escaped: a library path with `&` or `%` gets a clear error at
// setup instead of a silently broken autostart.
function windowsLauncher({ exe, env, httpPort = 8484, tlsPort = 8485 }) {
  const bad = /[&|<>^%!"\r\n]/;
  const lines = ["@echo off"];
  for (const [k, v] of Object.entries(env)) {
    if (!/^[A-Z0-9_]+$/.test(k)) throw new Error(`bad setting name: ${k}`);
    if (bad.test(String(v))) throw new Error(`the path for ${k} contains a character Windows can't start with: ${v}`);
    lines.push(`set "${k}=${v}"`);
  }
  if (bad.test(exe)) throw new Error("the install path contains characters Windows can't start with");
  lines.push(`"${exe}" constellation --host 0.0.0.0 --port ${httpPort} --tls-port ${tlsPort}`);
  return lines.join("\r\n") + "\r\n";
}

async function installLinux({ exe, envFile }) {
  const dir = path.join(os.homedir(), ".config", "systemd", "user");
  fs.mkdirSync(dir, { recursive: true });
  const unitPath = path.join(dir, UNIT);
  const existing = fs.existsSync(unitPath) ? fs.readFileSync(unitPath, "utf8") : null;
  if (!mayWriteUnit(existing)) {
    return { ok: false, error: `a Constellation service set up by hand already exists (${unitPath}); leaving it alone` };
  }
  fs.writeFileSync(unitPath, systemdUnit({ exe, envFile }));
  const steps = [
    ["systemctl", ["--user", "daemon-reload"]],
    ["systemctl", ["--user", "enable", "--now", UNIT]],
  ];
  for (const [c, a] of steps) {
    const r = await sh(c, a);
    if (!r.ok) return { ok: false, error: `${c} ${a.join(" ")}: ${r.err.trim()}` };
  }
  // linger = keep running with nobody logged in (a server in a closet). Best
  // effort: it needs polkit on some distros; without it we still start at login.
  const linger = await sh("loginctl", ["enable-linger", os.userInfo().username]);
  return { ok: true, linger: linger.ok, unit: path.join(dir, UNIT) };
}

async function installWindows({ exe, env, dataDir }) {
  const launcher = path.join(dataDir, "start-constellation.cmd");
  fs.writeFileSync(launcher, windowsLauncher({ exe, env }));
  const xmlPath = path.join(dataDir, "constellation-task.xml");
  const user = `${process.env.USERDOMAIN || os.hostname()}\\${os.userInfo().username}`;
  // UTF-16LE with BOM, as schtasks expects for the encoding the XML declares
  fs.writeFileSync(xmlPath, Buffer.concat([Buffer.from([0xff, 0xfe]),
    Buffer.from(windowsTaskXml({ launcher, user }), "utf16le")]));
  const r = await sh("schtasks.exe", ["/Create", "/TN", TASK, "/XML", xmlPath, "/F"]);
  if (!r.ok) return { ok: false, error: `schtasks: ${r.err.trim()}` };
  await sh("schtasks.exe", ["/Run", "/TN", TASK]);
  // Windows Firewall: private networks only (the family LAN), never public.
  await sh("netsh", ["advfirewall", "firewall", "add", "rule", "name=Constellation",
    "dir=in", "action=allow", "protocol=TCP", "localport=8484,8485", "profile=private"]);
  return { ok: true, task: TASK };
}

function parseEnvFile(text) {
  const env = {};
  for (const line of String(text).split(/\r?\n/)) {
    const m = line.match(/^([A-Z0-9_]+)=(.*)$/);
    if (m) env[m[1]] = m[2];
  }
  return env;
}

async function install({ exe, envFile, dataDir }) {
  if (process.platform === "win32") {
    return installWindows({ exe, dataDir, env: parseEnvFile(fs.readFileSync(envFile, "utf8")) });
  }
  if (process.platform === "linux") return installLinux({ exe, envFile });
  return { ok: false, error: "This system isn't supported yet (Linux and Windows are)." };
}

module.exports = { systemdUnit, windowsTaskXml, windowsLauncher, parseEnvFile, install, mayWriteUnit,
  UNIT, TASK, MARKER };
