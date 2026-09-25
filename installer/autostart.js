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
    // a start-rate limit belongs in [Unit]: systemd silently ignores it in
    // [Service], which used to leave the unit without the protection it names
    "StartLimitIntervalSec=0",
    "",
    "[Service]",
    "Type=simple",
    `EnvironmentFile=${envFile}`,
    `ExecStart=${sdQuote(exe)} constellation --host 0.0.0.0 --port ${httpPort} --tls-port ${tlsPort}`,
    "Restart=always",
    "RestartSec=5",
    // a picture frame must come back no matter how many times it falls over
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

function fileSnapshot(file) {
  return fs.existsSync(file) ? fs.readFileSync(file) : null;
}

function restoreFile(file, snapshot, errors, label) {
  try {
    if (snapshot == null) {
      if (fs.existsSync(file)) fs.unlinkSync(file);
    } else {
      fs.writeFileSync(file, snapshot);
    }
  } catch (e) { errors.push(`${label}: ${String((e && e.message) || e)}`); }
}

function rollbackResult(errors) {
  return errors.length ? { ok: false, error: errors.join("; ") } : { ok: true };
}

async function installLinux({ exe, envFile }, runtime = {}) {
  const rawRun = runtime.run || sh;
  const run = async (...args) => {
    try { return await rawRun(...args); }
    catch (e) { return { ok: false, out: "", err: String((e && e.message) || e) }; }
  };
  const home = runtime.home || os.homedir();
  const user = runtime.user || os.userInfo().username;
  const dir = path.join(home, ".config", "systemd", "user");
  fs.mkdirSync(dir, { recursive: true });
  const unitPath = path.join(dir, UNIT);
  const existing = fs.existsSync(unitPath) ? fs.readFileSync(unitPath, "utf8") : null;
  if (!mayWriteUnit(existing)) {
    return { ok: false, error: `a Constellation service set up by hand already exists (${unitPath}); leaving it alone` };
  }
  let priorEnabled = false;
  let priorActive = false;
  if (existing != null) {
    priorEnabled = (await run("systemctl", ["--user", "is-enabled", UNIT])).ok;
    priorActive = (await run("systemctl", ["--user", "is-active", UNIT])).ok;
  }
  fs.writeFileSync(unitPath, systemdUnit({ exe, envFile }));
  // Verify BEFORE enabling: systemd-analyze warns about an unknown key even
  // when it exits 0, and a warned unit can mean a protection that never runs
  // (StartLimitIntervalSec in the wrong section did exactly that). A silent
  // verify is the only "yes".
  const verify = await run("systemd-analyze", ["verify", unitPath]);
  if (!verify.ok || verify.err.trim() !== "") {
    const errors = [];
    restoreFile(unitPath, existing == null ? null : Buffer.from(existing), errors, "restore unit");
    return { ok: false, error: `systemd-analyze verify did not pass: ${verify.err.trim() || "exit nonzero"}` +
      (errors.length ? `; rollback failed: ${errors.join("; ")}` : "") };
  }
  const steps = [
    ["systemctl", ["--user", "daemon-reload"]],
    ["systemctl", ["--user", "enable", "--now", UNIT]],
  ];
  for (const [c, a] of steps) {
    const r = await run(c, a);
    if (!r.ok) {
      const errors = [];
      if (c === "systemctl" && a.includes("enable"))
        await run("systemctl", ["--user", "disable", "--now", UNIT]);
      restoreFile(unitPath, existing == null ? null : Buffer.from(existing), errors, "restore unit");
      await run("systemctl", ["--user", "daemon-reload"]);
      return { ok: false, error: `${c} ${a.join(" ")}: ${r.err.trim()}` +
        (errors.length ? `; rollback failed: ${errors.join("; ")}` : "") };
    }
  }
  // linger = keep running with nobody logged in (a server in a closet). Best
  // effort: it needs polkit on some distros; without it we still start at login.
  const linger = await run("loginctl", ["enable-linger", user]);
  const rollback = async () => {
    const errors = [];
    const disabled = await run("systemctl", ["--user", "disable", "--now", UNIT]);
    if (!disabled.ok) errors.push(`disable unit: ${disabled.err.trim()}`);
    restoreFile(unitPath, existing == null ? null : Buffer.from(existing), errors, "restore unit");
    const reload = await run("systemctl", ["--user", "daemon-reload"]);
    if (!reload.ok) errors.push(`daemon-reload: ${reload.err.trim()}`);
    if (existing != null && priorEnabled) {
      const enabled = await run("systemctl", ["--user", "enable", UNIT]);
      if (!enabled.ok) errors.push(`restore enabled unit: ${enabled.err.trim()}`);
    }
    if (existing != null && priorActive) {
      const active = await run("systemctl", ["--user", "start", UNIT]);
      if (!active.ok) errors.push(`restore active unit: ${active.err.trim()}`);
    }
    return rollbackResult(errors);
  };
  return { ok: true, linger: linger.ok, unit: unitPath, started: true,
    startsOnLogin: true, startsOnBoot: linger.ok, rollback };
}

async function installWindows({ exe, env, dataDir }, runtime = {}) {
  const rawRun = runtime.run || sh;
  const runCommand = async (...args) => {
    try { return await rawRun(...args); }
    catch (e) { return { ok: false, out: "", err: String((e && e.message) || e) }; }
  };
  const launcher = path.join(dataDir, "start-constellation.cmd");
  const previousLauncher = fileSnapshot(launcher);
  const xmlPath = path.join(dataDir, "constellation-task.xml");
  const previousXml = fileSnapshot(xmlPath);
  const previousTask = await runCommand("schtasks.exe", ["/Query", "/TN", TASK, "/XML"]);
  const taskXml = previousTask.ok && previousTask.out.trim() ? previousTask.out : null;
  const previousFirewall = await runCommand("netsh", ["advfirewall", "firewall", "show", "rule", "name=Constellation"]);
  const firewallExisted = previousFirewall.ok && /Constellation/i.test(previousFirewall.out);
  let taskChanged = false;
  let firewallAdded = false;
  let rolledBack = false;

  const rollback = async () => {
    if (rolledBack) return { ok: true };
    rolledBack = true;
    const errors = [];
    if (firewallAdded) {
      const r = await runCommand("netsh", ["advfirewall", "firewall", "delete", "rule", "name=Constellation"]);
      if (!r.ok) errors.push(`firewall cleanup: ${r.err.trim()}`);
    }
    if (taskChanged) {
      if (taskXml != null) {
        const restoreXml = path.join(dataDir, ".constellation-task-restore.xml");
        try {
          fs.writeFileSync(restoreXml, Buffer.concat([Buffer.from([0xff, 0xfe]), Buffer.from(taskXml, "utf16le")]));
          const r = await runCommand("schtasks.exe", ["/Create", "/TN", TASK, "/XML", restoreXml, "/F"]);
          if (!r.ok) errors.push(`task restore: ${r.err.trim()}`);
        } catch (e) { errors.push(`task restore: ${String((e && e.message) || e)}`); }
        finally { try { if (fs.existsSync(restoreXml)) fs.unlinkSync(restoreXml); } catch (e) {
          errors.push(`task restore file cleanup: ${String((e && e.message) || e)}`); } }
      } else {
        const r = await runCommand("schtasks.exe", ["/Delete", "/TN", TASK, "/F"]);
        if (!r.ok) errors.push(`task cleanup: ${r.err.trim()}`);
      }
    }
    restoreFile(launcher, previousLauncher, errors, "restore launcher");
    restoreFile(xmlPath, previousXml, errors, "restore task XML");
    return rollbackResult(errors);
  };

  fs.writeFileSync(launcher, windowsLauncher({ exe, env }));
  const user = runtime.user || `${process.env.USERDOMAIN || os.hostname()}\\${os.userInfo().username}`;
  // UTF-16LE with BOM, as schtasks expects for the encoding the XML declares
  fs.writeFileSync(xmlPath, Buffer.concat([Buffer.from([0xff, 0xfe]),
    Buffer.from(windowsTaskXml({ launcher, user }), "utf16le")]));
  const create = await runCommand("schtasks.exe", ["/Create", "/TN", TASK, "/XML", xmlPath, "/F"]);
  taskChanged = create.ok;
  if (!create.ok) {
    const rb = await rollback();
    return { ok: false, error: `schtasks /Create: ${create.err.trim()}` + (!rb.ok ? `; rollback failed: ${rb.error}` : "") };
  }
  // Startup must be verified, not assumed: a task that exists but refuses to
  // start is a picture frame that stays dark after the next reboot.
  const run = await runCommand("schtasks.exe", ["/Run", "/TN", TASK]);
  if (!run.ok) {
    const rb = await rollback();
    return { ok: false, error: `schtasks /Run: ${run.err.trim()}` + (!rb.ok ? `; rollback failed: ${rb.error}` : "") };
  }
  // Windows Firewall: private networks only (the family LAN), never public.
  if (!firewallExisted) {
    const fw = await runCommand("netsh", ["advfirewall", "firewall", "add", "rule", "name=Constellation",
      "dir=in", "action=allow", "protocol=TCP", "localport=8484,8485", "profile=private"]);
    firewallAdded = fw.ok;
    if (!fw.ok) {
      const rb = await rollback();
      return { ok: false, error: `firewall rule failed: ${fw.err.trim()}` + (!rb.ok ? `; rollback failed: ${rb.error}` : "") };
    }
  }
  return { ok: true, task: TASK, started: true, startsOnLogin: true,
    startsOnBoot: false, rollback };
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

module.exports = { systemdUnit, windowsTaskXml, windowsLauncher, parseEnvFile, install, installLinux, installWindows, mayWriteUnit,
  UNIT, TASK, MARKER };
