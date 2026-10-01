# Constellation V2 — Build Plan

**Status:** ACTIVE. Written 2026-09-23.
**Authority:** `CONSTELLATION-APP-SPEC-V2.md`. Where this plan and the spec disagree, the spec wins and this plan gets fixed.
Every checkpoint merges only after independent review of the exact tree.

---

## 0. How to read this plan

- Work is cut into **checkpoints (CP)**. Each one ends in a runnable artifact plus **acceptance evidence** (test output, recorded run, screenshots from a real device). Nothing is "done" on a green unit test alone.
- **TDD-first:** each slice starts with a failing test that states the behaviour, then the code.
- **Tests ratchet:** the suite count never goes down. Mutation checks on every security and privacy rule.
- **No stubs shipped.** A feature that isn't wired end to end is labelled "not built", never "done".
- **One variable at a time on representative hardware.** New builds are tested in an isolated environment before any production rollout.

## 1. Where code lives

**One public repository: `constellation`.** Engine, server, web UI, installer, Android/iOS apps, deploy scripts, specs, ADRs and this plan all live here.

| Concern | Path |
|---|---|
| Engine (`memoryvault` pipeline, server, web UI) | `scripts/memoryvault/`, tests in `scripts/tests/` |
| Installer (Electron) | `installer/` |
| Android / iOS companion | `android/`, `ios/` (CP5/CP6) |
| Deploy + systemd | `deploy/` |
| Specs, ADRs, evidence | `docs/` |

**Privacy rule:** nothing household-specific is ever committed. Names, addresses, hostnames, real paths, topology, live counts, interview material, attributed speech, internal roles and relationship details remain private. Public documents contain product requirements, neutral ADRs and generic deployment examples only. Code ships generic defaults. `tools/leak_scan.py` scans HEAD, index and worktree bytes in CI and the pre-push hook; examples use a synthetic public cast.

## 2. Invariants (every checkpoint re-proves these)

1. **LAN-only.** No cloud, no telemetry, nothing exposed to the internet. Proven by an egress-capture test in CI (CP0) that fails on any off-LAN connection during a full pipeline run.
2. **Originals are never touched.** Discovery, import, sync and culling only read sources. Proven by checksum-before/after tests.
3. **The vault wall.** Vault content never reaches any derivative surface: thumbnails, faces, Memories graph, placards, search, backups or exports. Proven by one automated test that grows with every new derivative (CP8).
4. **Backup of record.** A phone delete never deletes a server copy (CP5).
5. **Quiet by default.** No per-upload notifications, ever.
6. **Open frames, PIN-gated tools.** Wall, Memories and ambient never ask for a password. Curation, people, vault, sync settings, health and backup always do (CP1).

## 3. Spec-vs-code conflicts found in the audit (resolved here)

| # | Conflict | Resolution in this plan |
|---|---|---|
| X1 | The server has **no authentication at all**; `/curation`, `/api/purge`, vault and markdelete endpoints are open to the LAN | CP1 closes this **before** anything new is exposed |
| X2 | The installer is GPU-centric (VRAM-based model pick); spec A3 says no GPU needed | CP2 makes CPU the default path; GPU becomes an accelerator |
| X3 | The installer still builds a macOS DMG; spec A2 defers macOS | CP2 removes the target |
| X4 | Email photo sharing (`/api/photo/share`) is shipped; spec F3 says external sharing is OUT | CP1 moves it behind the PIN and turns it **off by default**. Removal remains an explicit product decision (Q4) |
| X5 | The vault uses **one shared passphrase** (`vault-ceremony.sh`); spec D4 requires per-user secrets | CP8 includes a migration from the shared vault; the existing vault is never re-screened (spec D2) |
| X6 | HEIC is decoded for display only; spec B5 converts to JPEG at ingest | CP3 |
| X7 | Ingest order is `ORDER BY id`; spec B6 wants recent-first | CP3 |
| X8 | Only the nightly SQLite snapshot exists (database only); spec A5 needs library + database backup and restore | CP10 |
| X9 | There's no per-user or ownership model anywhere in the schema | CP7 is a prerequisite for kids' shelves, per-user vaults and display identities |

## 4. Checkpoints

### CP0 — Foundations (no user-visible change)
- **Publish:** land the scrubbed spec and this plan on a reviewed branch. Raw interviews and infrastructure planning never enter the public repository.
- **Test parity:** keep all product tests in the public tree.
- **Public master:** ~~merge the vault dir-mode fix~~ already there (4cd4333).
- **CI:** one test entry point per repo; Python suite and installer unit tests; the egress-capture test (invariant 1).
- **Decision records** (short ADRs, written before any code):
  - the helper protocol (CP4);
  - the sync protocol (CP5);
  - the identity and ownership model (CP7);
  - the per-user vault crypto (CP8);
  - the update channel and signing (CP10).
- **Spikes, time-boxed and written up:**
  - Windows packaging: NSIS plus a scheduled task for the server, running the Python backend from PyInstaller.
  - iOS build path (needs Q1).
- **Acceptance:** CI is green; the egress test fails on a deliberately added outbound call and passes without it; the ADRs receive independent review.

### CP1 — Security baseline (spec A6)
- **Route classification table:** every route is either `open-display` or `pin`. A test fails on any unclassified route.
- **Family PIN:**
  - hashed (argon2id), with rate limiting and lockout;
  - server-side sessions (HttpOnly, Secure, SameSite=Strict);
  - CSRF protection on every mutating request.
- **HTTPS by default through the family CA** (v1 Phase A pattern): the installer creates the CA, the server serves TLS, and a plain-HTTP request to a PIN route is refused.
- Email sharing sits behind the PIN and is off by default (X4).
- **Acceptance:** an unauthenticated LAN client gets 401 on every PIN route (the matrix test) and full access to the wall. Mutation checks cover: a route left unclassified, PIN compared without constant time, a missing rate limit, and a CSRF bypass.

### CP2 — Installer v2 (spec gates 1 and 9-partial)
- **Six-step wizard:** Welcome → System scan → Storage & sources → Downloads → First sweep → Finish. Plus a "Show details" pane with paths, model choice and a live log tail.
- **Hardware floor check:** 8 GB RAM and 200 GB free, in plain words, with bundled offline guidance and no external shopping link. CPU is the default model path (X2).
- **Linux:**
  - AppImage;
  - a systemd **user** unit for the server;
  - autostart;
  - uninstall that leaves the family's photos in place.
- **Windows:**
  - NSIS installer;
  - the server runs as a scheduled task at logon, with a tray icon;
  - firewall rule scoped to the private network profile.
- The macOS target is removed (X3).
- The wizard sets the PIN and walks the backup drive step (backup is mandatory, spec A5).
- **Acceptance:** on clean Ubuntu 26.04 and Windows 11 test machines, a non-technical run finishes without a terminal, and the wall shows photos the same night. Screen recordings remain private evidence.

### CP3 — First run and ingest quality (spec gate 2)
- **Recent-first sweep:** the ~2,000 newest photos first, then an overnight backfill with honest progress (X7).
- **"Patient sky":** a count-up during the first sweep, and stars appear as photos are read.
- **Formats:**
  - HEIC and Live Photos become a JPEG derivative at ingest; originals are untouched (X6);
  - RAW files (CR2, CR3, ARW, NEF) are archive-only;
  - files over 4 GB are refused with a plain-words warning;
  - the storage step shows the maths.
- **Culling:**
  - exact duplicates: hash match, keep one;
  - bursts: keep the sharpest by a sharpness metric and **park** the rest (recoverable, never deleted).
- **Acceptance:** a synthetic fixture library with bursts, HEIC, RAW and a 4 GB+ file ingests correctly. A representative large-library benchmark reaches a visible sky within minutes; raw benchmark inventory stays private.

### CP4 — Helper app and import engine (spec gate 3)
- **Helper:** a small, read-only daemon for Linux and Windows.
  - Advertises itself over mDNS.
  - Consent is given once, on that machine, in that user's session.
  - Pairing uses a short code, then a pinned TLS channel.
  - Offers list-folders and stream-file only. There is no write, move or delete in the protocol at all.
- **Machine cards in the wizard:** e.g. "the other parent's laptop — 3 photo folders found".
- **Import engine:** chunked and resumable (it survives a reboot mid-import), with honest time estimates.
- **Acceptance:** a two-machine LAN test; the import is killed and resumed with no duplicates and no missing files. Security tests prove a malicious client cannot make the helper write or reach outside the folders the user consented to.

### CP5 — Sync engine and Android upload (spec gates 4 and 5; the headline)
- **Sync API:**
  - per-device credentials, paired with a PIN-approved code;
  - resumable, content-hash-idempotent uploads;
  - edits stored as new versions, originals kept;
  - Wi-Fi-only enforced on the client and the LAN-only rule on the server.
- **Android:** extends the existing Kotlin app.
  - Background upload through WorkManager and MediaStore, with a one-time permission.
  - A "Free up space" flow that shows only photos the server has **verified** (checked against the server's hash).
- **Backup semantics:** a phone delete never removes the server copy (invariant 4).
- **Acceptance:** on a test Android phone, take photos, return to the configured LAN, and they appear on the wall with no taps. Delete them on the phone and they stay on the server. Kill the app mid-upload and it resumes. An independent public-beta household installs at the end of this checkpoint (spec §11; needs Q3).

### CP6 — iOS companion (spec gates 4 and 5 on iPhone)
- A Swift thin shell with PhotoKit upload: aggressive upload on Wi-Fi at home, plus top-ups on app open. The iOS limits are owned honestly in the app copy.
- Distributed through TestFlight.
- **Blocked on Q1 and Q2** (a Mac to build on, and the Apple Developer account). Android goes first regardless.

### CP7 — People, ownership and kids' shelves (prerequisite for CP8 and CP9)
- **Identity model:**
  - family members: adults and kids;
  - devices that belong to a person;
  - every photo has an owner.
- **Kids:**
  - opt-in per kid;
  - a private shelf visible only to that kid (plus the parents' upload log);
  - a per-photo "share to family" choice.
- **Acceptance:** a permission matrix test across adult, kid, another kid and a display, covering every read surface.

### CP8 — Per-user vaults (spec gates 6 and 7)
- **Per-user vault secrets:**
  - optional sharing at setup;
  - the LUKS recovery key is shown once and stored in a trusted password manager;
  - a "lost = unrecoverable" ceremony with a required checkbox;
  - the family PIN never opens a vault.
- Migration from the v1 shared vault (X5).
- **Routing:**
  - two doors: classifier auto-routing at ingest, and folder declaration at import;
  - a release queue behind the PIN, one tap to release, zero notifications.
- **The wall test** (invariant 3): walks every derivative producer, fails if any vault item reaches one, and has to be extended whenever a new derivative is added.
- **Minor-harm escalation:** the policy text must come from **legal review before the classifier ships** (Q5). Until then, auto-routing stays off and only folder declaration is live.
- **Acceptance:** the wall test is mutation-checked (each derivative's filter removed in turn must fail it), and the key ceremony and recovery are run on real hardware.

### CP9 — Displays and control (spec gate 8)
- **Display identity:** family, or personal (one person's shelf). A kid's room runs that kid's own sky.
- A control channel from any phone ("show Christmas 2019 on the wall"), with PIN-scoped permissions.
- **Companion roles:** Display, Browse, Upload and Control, chosen per device.
- **Acceptance:** a leak test. Family mode never shows shelf content, and personal mode never leaks to family surfaces.

### CP10 — Updates, backup/restore and health (spec gates 9 and 10)
- **Updates:**
  - a signed update channel;
  - installs only inside the 3–5 AM window, and never mid-memory (waits for a slideshow boundary);
  - automatic rollback on a failed health check;
  - a manual "check for updates" button.
- **Backup and restore:**
  - covers the library and the database, to a dedicated external drive;
  - encrypted;
  - a scheduled restore test;
  - the vault is backed up separately and stays encrypted.
- **Health:**
  - a drive alert at 70% full;
  - database and disk integrity checks;
  - restart-safe self-repair;
  - plain-language alerts to an adult.
- **Acceptance:** force a failed update and watch it roll back. Restore onto a clean box and compare checksums. Fill a test disk to 70% and the alert arrives.

### CP11 — Release
- A full regression on Linux and Windows.
- The egress test on a full run.
- A security review of the exact tree.
- An isolated local pilot runs it first.
- The release is tagged from a reviewed immutable SHA.

## 5. Order and parallelism

`CP0 → CP1 → CP2 → CP3 → (CP4 ∥ CP5) → CP6 → CP7 → CP8 → CP9 → CP10 → CP11`

- CP1 must land before anything that opens new network surface (CP4 and CP5).
- CP7 must land before CP8 and CP9.
- CP6 runs whenever Q1 and Q2 are resolved.
- CP10's update channel can start early, but it ships last.

## 6. Open product decisions

- **Q1:** Is there a Mac available for iOS builds (Xcode)? If not: a CI macOS runner, or a used Mac mini.
- **Q2:** Enrol in the Apple Developer Program ($99/yr) before CP6.
- **Q3:** Recruit through a public beta program (spec G2) before the end of CP5; keep participant identity and relationship details outside the repository.
- **Q4:** Remove email sharing entirely, or keep it PIN-gated and off by default? (X4)
- **Q5:** legal review drafts the minor-harm escalation text before CP8's classifier ships.
- **Q6:** A clean Windows 11 test machine for CP2 and CP4.

## 7. Evidence policy

Checkpoint evidence records the reviewed commit, test counts, mutation results, and generic platform class. Public evidence may include synthetic fixtures and screenshots that have passed the privacy scan. Raw interviews, deployment logs, machine identities, addresses, storage labels, household inventory, relationship details, and recordings from lived-in systems remain outside the repository.

Current checkpoint outcomes are represented by the executable tests and commit history. Any narrative evidence published later must pass `tools/leak_scan.py` and independent semantic review before it is committed.
