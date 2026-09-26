// Linux packaging contract: Ubuntu 24.04+ blocks unprivileged user namespaces
// (kernel.apparmor_restrict_unprivileged_userns=1). Electron's sandbox needs one,
// so an unconfined AppImage aborts at launch (SIGTRAP, "chrome-sandbox ... 4755").
// The supported Linux install is a .deb that ships an AppArmor profile granting
// `userns` to exactly the installed binary, loads it on install, and removes it
// on uninstall. Run: node --test test/linux-packaging.test.js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const ROOT = path.resolve(__dirname, "..");
const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8"));
const build = pkg.build;
const BIN = "/opt/Constellation Setup/constellation-setup";

function readRel(rel) {
  return fs.readFileSync(path.join(ROOT, rel), "utf8");
}

// electron-builder renders ${macro} in maintainer-script templates; unknown macros throw.
function render(tpl) {
  return tpl.replace(/\$\{([a-zA-Z]+)\}/g, (_, k) => {
    const vars = { executable: "constellation-setup", sanitizedProductName: "Constellation Setup",
                   productFilename: "Constellation Setup" };
    if (!(k in vars)) throw new Error(`undefined macro ${k}`);
    return vars[k];
  });
}

test("linux targets: deb is the primary target, AppImage kept as secondary", () => {
  const targets = build.linux.target.map((t) => (typeof t === "string" ? t : t.target));
  assert.equal(targets[0], "deb");
  assert.ok(targets.includes("AppImage"));
  assert.equal(pkg.scripts["dist:linux"],
    "chmod 0644 linux/apparmor/constellation-setup && electron-builder --linux deb AppImage");
  // fpm copies the source mode into the package; a group-writable checkout (umask 002)
  // would ship a group-writable security profile.
  assert.equal(fs.statSync(path.join(ROOT, "linux/apparmor/constellation-setup")).mode & 0o022, 0);
});

test("deb installs the AppArmor profile as a file at /etc/apparmor.d", () => {
  const extra = build.deb.extraFiles || build.linux.extraFiles || [];
  const hit = extra.find((e) => e.to === "/etc/apparmor.d/constellation-setup" ||
                                (e.to === "../../etc/apparmor.d/constellation-setup"));
  assert.ok(build.deb.fpm, "deb.fpm must map the profile into /etc (extraFiles land under /opt)");
  const mapping = build.deb.fpm.find((a) => a.endsWith("=/etc/apparmor.d/constellation-setup"));
  assert.ok(mapping || hit, "profile must be installed to /etc/apparmor.d/constellation-setup");
  if (mapping) {
    const src = mapping.split("=")[0];
    assert.ok(fs.existsSync(path.join(ROOT, src)), `profile source ${src} missing`);
  }
  assert.ok(build.deb.depends.includes("apparmor") || (build.deb.recommends || []).includes("apparmor"));
  // electron-builder appends deb.fpm entries after the path arguments, so a flag there is
  // treated as a path ("Cannot package the path './--flag'"). Only src=dest mappings allowed.
  for (const arg of build.deb.fpm) assert.match(arg, /^[^-][^=]*=\/\S/, `fpm entry ${arg} must be a src=dest mapping`);
});

test("AppArmor profile grants userns to exactly the installed binary and nothing else", () => {
  const profile = readRel("linux/apparmor/constellation-setup");
  assert.match(profile, /^abi <abi\/4\.0>,$/m);
  assert.match(profile, /^include <tunables\/global>$/m);
  const header = profile.match(/^profile (\S+) "([^"]+)" flags=\(unconfined\) \{$/m);
  assert.ok(header, "profile header must quote the path (it contains a space)");
  assert.equal(header[1], "constellation-setup");
  assert.equal(header[2], BIN);
  const body = profile.slice(profile.indexOf("{") + 1, profile.lastIndexOf("}"));
  const rules = body.split("\n").map((l) => l.trim()).filter((l) => l && !l.startsWith("#"));
  assert.deepEqual(rules, ["userns,", "include if exists <local/constellation-setup>"]);
  const code = profile.split("\n").filter((l) => !l.trim().startsWith("#")).join("\n");
  assert.doesNotMatch(code, /\/tmp|\*|\.mount_|AppImage/, "never widen to AppImage mounts or globs");
});

test("AppArmor profile parses with the system apparmor_parser when available", (t) => {
  let parser;
  try { parser = execFileSync("sh", ["-c", "command -v apparmor_parser"], { encoding: "utf8" }).trim(); }
  catch { return t.skip("apparmor_parser not installed"); }
  if (!parser) return t.skip("apparmor_parser not installed");
  // -Q: parse/compile only, never load; -K: skip the kernel cache. No root needed.
  execFileSync(parser, ["-QK", path.join(ROOT, "linux/apparmor/constellation-setup")], { stdio: "pipe" });
});

test("after-install loads the profile before anything can launch, and tolerates no apparmor", () => {
  assert.equal(build.deb.afterInstall, "linux/deb/after-install.sh");
  const script = render(readRel("linux/deb/after-install.sh"));
  const load = script.indexOf("apparmor_parser -r -W -T /etc/apparmor.d/constellation-setup");
  assert.ok(load >= 0, "must (re)load the shipped profile");
  assert.match(script, /if \[ -d \/sys\/kernel\/security\/apparmor \] && command -v apparmor_parser/);
  assert.match(script, /\|\| true/, "a load failure must not abort package installation");
  // Keep electron-builder's own steps (/usr/bin link, chrome-sandbox mode, desktop db).
  assert.match(script, /update-alternatives --install '\/usr\/bin\/constellation-setup'/);
  assert.match(script, /chmod 0755 '\/opt\/Constellation Setup\/chrome-sandbox'/);
  assert.doesNotMatch(script, /apparmor_restrict_unprivileged_userns=0|sysctl -w/,
                      "never disable the system-wide restriction");
});

test("after-remove unloads and deletes the profile only on remove/purge", () => {
  assert.equal(build.deb.afterRemove, "linux/deb/after-remove.sh");
  const script = render(readRel("linux/deb/after-remove.sh"));
  assert.match(script, /case "\$1" in\s+remove\|purge\)/);
  assert.match(script, /apparmor_parser -R \/etc\/apparmor\.d\/constellation-setup/);
  assert.match(script, /rm -f \/etc\/apparmor\.d\/constellation-setup/);
  assert.match(script, /update-alternatives --remove 'constellation-setup'/);
});

test("maintainer scripts are valid POSIX-ish shell", () => {
  for (const rel of ["linux/deb/after-install.sh", "linux/deb/after-remove.sh"]) {
    const out = path.join(require("node:os").tmpdir(), `cst-${path.basename(rel)}`);
    fs.writeFileSync(out, render(readRel(rel)));
    execFileSync("bash", ["-n", out]);
    fs.unlinkSync(out);
  }
});

test("built .deb (if present): profile is a root-owned 0644 conffile and scripts are wired", (t) => {
  const deb = process.env.CONSTELLATION_DEB;
  if (!deb) return t.skip("set CONSTELLATION_DEB=/path/to/constellation-setup_*.deb to inspect a build");
  const listing = execFileSync("dpkg-deb", ["-c", deb], { encoding: "utf8" });
  const line = listing.split("\n").find((l) => l.endsWith("./etc/apparmor.d/constellation-setup"));
  assert.ok(line, "profile missing from package");
  assert.match(line, /^-rw-r--r-- (root\/root|0\/0) /, `profile must be root:root 0644, got: ${line}`);
  assert.equal(execFileSync("dpkg-deb", ["-f", deb, "Depends"], { encoding: "utf8" }).trim().includes("apparmor"), true);
  const tmp = fs.mkdtempSync(path.join(require("node:os").tmpdir(), "cst-ctl-"));
  try {
    execFileSync("dpkg-deb", ["-e", deb, tmp]);
    assert.equal(fs.readFileSync(path.join(tmp, "conffiles"), "utf8").trim().split("\n")
      .includes("/etc/apparmor.d/constellation-setup"), true);
    assert.match(fs.readFileSync(path.join(tmp, "postinst"), "utf8"), /apparmor_parser -r -W -T \/etc\/apparmor\.d\/constellation-setup/);
    assert.match(fs.readFileSync(path.join(tmp, "postrm"), "utf8"), /apparmor_parser -R \/etc\/apparmor\.d\/constellation-setup/);
  } finally { fs.rmSync(tmp, { recursive: true, force: true }); }
});
