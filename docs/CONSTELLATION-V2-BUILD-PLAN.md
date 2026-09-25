# Constellation V2 — Build Plan

**Status:** ACTIVE. Written 2026-09-23 by the build agent (AMS task #114).
**Authority:** `CONSTELLATION-APP-SPEC-V2.md` (approved by the founder 2026-09-23; the founder waived the the co-founder sign-off gate for this standalone project). `CONSTELLATION-V2-INTERVIEW.md` is the decision record. Where this plan and the spec disagree, the spec wins and this plan gets fixed.
**Integrator / exact-snapshot reviewer:** the reviewer. Every checkpoint merges only after his review of the exact tree.

---

## 0. How to read this plan

- Work is cut into **checkpoints (CP)**. Each one ends in a runnable artifact plus **acceptance evidence** (test output, recorded run, screenshots from a real device). Nothing is "done" on a green unit test alone.
- **TDD-first:** each slice starts with a failing test that states the behaviour, then the code.
- **Tests ratchet:** the suite count never goes down. Mutation checks on every security and privacy rule.
- **No stubs shipped.** A feature that isn't wired end to end is labelled "not built", never "done".
- **One variable at a time on real hardware.** The family's live install (the home server + the TV displays) is production. New builds are tested on a separate box first.

## 1. Where code lives

**One public repository: `constellation`** (decided by the founder, 2026-09-23). Engine, server, web UI, installer, Android/iOS apps, deploy scripts, specs, ADRs and this plan all live here. The earlier private engine repo is archived read-only; its one-way sync tool is retired.

| Concern | Path |
|---|---|
| Engine (`memoryvault` pipeline, server, web UI) | `scripts/memoryvault/`, tests in `scripts/tests/` |
| Installer (Electron) | `installer/` |
| Android / iOS companion | `android/`, `ios/` (CP5/CP6) |
| Deploy + systemd | `deploy/` |
| Specs, ADRs, evidence | `docs/` |

**Privacy rule (replaces the old scrub-on-sync):** nothing household-specific is ever committed. Names, IPs, hostnames and real paths live only in each install's local settings file (`~/Constellation/.env`, `MEMORYVAULT_*` variables, e.g. `MEMORYVAULT_VAULT_FOLDERS`, `MEMORYVAULT_SOURCES_FILE`). Code ships generic defaults. Enforced on every push by `tools/leak_scan.py` in CI (and the pre-push hook); examples in tests and docs use the public cast (Alex, Bailey, Casey, Dana, Elliot).

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
| X4 | Email photo sharing (`/api/photo/share`) is shipped; spec F3 says external sharing is OUT | CP1 moves it behind the PIN and turns it **off by default**. Removing it entirely needs the founder's call (Q4) |
| X5 | The vault uses **one shared passphrase** (`vault-ceremony.sh`); spec D4 requires per-user secrets | CP8 includes a migration from the shared vault; the existing vault is never re-screened (spec D2) |
| X6 | HEIC is decoded for display only; spec B5 converts to JPEG at ingest | CP3 |
| X7 | Ingest order is `ORDER BY id`; spec B6 wants recent-first | CP3 |
| X8 | Only the nightly SQLite snapshot exists (database only); spec A5 needs library + database backup and restore | CP10 |
| X9 | There's no per-user or ownership model anywhere in the schema | CP7 is a prerequisite for kids' shelves, per-user vaults and display identities |

## 4. Checkpoints

### CP0 — Foundations (no user-visible change)
- **Publish:** land the spec, interview and this plan on `spec/v2` byte-for-byte (sha256 recorded in the commit).
- **Test parity:** port the 11 tests missing from the public tree. Make `sync-public.py` copy the tests too, with scrubbing.
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
- **Acceptance:** both repos green in CI; the egress test fails on a deliberately added outbound call and passes without it; the ADRs are reviewed by the reviewer.

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
- **Hardware floor check:** 8 GB RAM and 200 GB free, in plain words, with a link to the shopping-list page. CPU is the default model path (X2).
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
- **Acceptance:** on a clean Ubuntu 26.04 box (gpu-box, .180) and a clean Windows 11 box, a non-technical run finishes without a terminal, and the wall shows photos the same night. Screen recordings are kept as evidence.

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
- **Acceptance:** a fixture library with bursts, HEIC, RAW and a 4 GB+ file ingests correctly. The sky is visible within minutes on the real ~22k library.

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
- **Acceptance:** on a real Android phone, take photos, walk home, and they're on the wall with no taps. Delete them on the phone and they stay on the server. Kill the app mid-upload and it resumes. The external beta family installs at the end of this checkpoint (spec §11; needs Q3).

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
  - the LUKS recovery key is shown once and stored in Vaultwarden;
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
  - covers the library and the database, to an external drive (the 4 TB Elements is the first target);
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
- This house runs it as beta first.
- The release is tagged from a reviewed immutable SHA.

## 5. Order and parallelism

`CP0 → CP1 → CP2 → CP3 → (CP4 ∥ CP5) → CP6 → CP7 → CP8 → CP9 → CP10 → CP11`

- CP1 must land before anything that opens new network surface (CP4 and CP5).
- CP7 must land before CP8 and CP9.
- CP6 runs whenever Q1 and Q2 are resolved.
- CP10's update channel can start early, but it ships last.

## 6. Open questions (the founder)

- **Q1:** Is there a Mac available for iOS builds (Xcode)? If not: a CI macOS runner, or a used Mac mini.
- **Q2:** Enrol in the Apple Developer Program ($99/yr) before CP6.
- **Q3:** Name the external beta family (spec G2) before the end of CP5.
- **Q4:** Remove email sharing entirely, or keep it PIN-gated and off by default? (X4)
- **Q5:** legal review drafts the minor-harm escalation text before CP8's classifier ships.
- **Q6:** A clean Windows 11 test machine for CP2 and CP4.

## 7. Evidence log

Each checkpoint appends: date, SHA(s), test counts before/after, mutation results, device and OS, and links to recordings or screenshots.

### CP0 — evidence (2026-09-23)
- Spec + interview imported byte-for-byte (`bcdf7bb`, sha256 in message); plan + README goals (`eeaac1b`); PR the private repository (archived)#30.
- Tests identity-neutral + ported verbatim; sync-public copies tests (was scan-only). Private 37 → **41**; public 26 → **41**. Leak guard mutation-checked (family name, private IP → sync aborts, public untouched).
- Egress guard `tests/test_egress.py` (4 tests): full pipeline through the real vision HTTP client + web pages; static no-CDN scan. Mutation-checked: telemetry URL, raw public-IP socket, CDN `<script>`, disabled guard → all fail.
- CI `.github/workflows/tests.yml` in both repos (engine tests + egress; private also runs a public-sync leak dry run).
- ADRs 001–005 proposed (helper, sync, identity, vault crypto, updates).
- **Remaining in CP0:** Windows packaging spike; iOS path (blocked on Q1/Q2); the reviewer review of ADRs.
- **New questions from ADRs:** Q7 — Windows vaults as age archives (no VeraCrypt)? Q8 — migration mapping of the v1 shared vault subfolders? Q9 — allow the single opt-in, send-nothing update check as the one exception to "nothing leaves the LAN", or manual-only updates?

### the founder's answers (2026-09-23)
- **Q4 email sharing:** keep it, behind the family PIN, **off by default** (CP1).
- **Q9 updates:** the single opt-in, send-nothing update check is **approved** as the one exception to LAN-only (ADR-005).
- **Q7 Windows vaults:** encrypted folders (age), **no VeraCrypt** (ADR-004).
- **Q8 v1 vault migration:** **no automatic mapping.** the founder sorts the existing `parent2` / `other` / `review` items by hand in a migration UI; nothing is moved until he does.
- **Q1 iOS build:** no Mac; use a **cloud macOS build service** (CI runner). CP6 plans around that.
- **Q2 Apple Developer account:** **wait until Android sync (CP5) works.**
- **Q6 Windows test machine:** the home server's Windows partition. Note: booting it takes the home server (AMS, agents, the live Constellation) offline for the test window, so Windows tests are batched and scheduled with the founder.
- **Q3 beta family:** the founder's cousin **the beta household**. Installs at the end of CP5.
- **Q5 escalation text:** still with legal review (requested via AMS), needed before CP8 auto-routing.

### CP1 — evidence (2026-09-23)
- `constellation/auth.py`: route table (open / pin / pin_write), unclassified → 404 before any handler; scrypt-hashed PIN (0600, never in clear), `mvault pin set` (prompted); lockout 5 free then 30s doubling to 15 min; server-side sessions (hash-keyed, 1h idle / 8h absolute, PIN change signs all out); HttpOnly+SameSite=Strict(+Secure over TLS) cookie; CSRF header on every mutation; PIN/login only over TLS, `X-Forwarded-Proto` trusted only from loopback proxy; `MEMORYVAULT_REQUIRE_TLS` typo fails closed; email sharing off by default and PIN-gated.
- `static/login.html`, `static/pin.js` (CSRF + 401→login) on all API-using pages.
- Tests: `tests/test_auth.py` 22 → suite 41 → **63**. 13 mutations all caught (2 found weak tests first — fixed by a hand-written private-route list and a forced unclassified-route test). Real bug caught: session alive at exactly 8h (`>` → `>=`).
- E2E via real Caddy TLS (test port): wall 200 without PIN; private 401; wrong PIN 401; right PIN 200; mutation w/o CSRF 403, with CSRF 200; share 404.
- **Not run:** cross-host LAN probe (blocked by the approval guard twice; covered by unit test of the loopback-only proxy rule). Re-run when approved.
- **Deploy note:** turning CP1 on for the live install requires `mvault pin set` + a Caddy site in front of :8484. Until then the live server is unchanged.

### CP2a — native HTTPS + family CA (engine side of the installer), 2026-09-23
- `constellation/tls.py` + `mvault tls init|status`: family CA (EC P-256, 10y) + server cert (397d, SANs = host/.local/localhost + LAN IPs), keys 0600, idempotent (CA reused so trusted devices stay trusted; reissue on name change or <30d).
- Server now answers HTTPS itself on `--tls-port` (default **8485**; 8443 is taken by Vaultwarden on the home server). Plain HTTP stays for the picture frame; private pages 308 → HTTPS; hostile Host headers get no redirect; `/ca.pem` served for phones/TVs; a busy HTTPS port never takes the frame down; failed handshakes from untrusting devices are quiet.
- Result: the installer needs **no Caddy** — PIN over TLS out of the box.
- Tests: `tests/test_tls.py` 14 → suite **77**. 11 mutations: 10 caught; T1 (temp file briefly 0644 before final chmod 0600) survives — final mode is tested, window is on a fresh 0700 dir; accepted. T9 first survived → test strengthened (4 hostile Host forms) → caught.
- E2E: real `mvault` server + openssl (TLSv1.3, verify 0), curl without CA refused (exit 60), and **real Chrome** (`docs/evidence/cp2/`): wall open, http /curation → https → /login, wrong PIN message, right PIN → curation, page fetch carries CSRF, session cookie HttpOnly — 6/6.

### CP2b — installer v2 (constellation#5), 2026-09-23
- Wizard: 6 steps + Show details; 8 GB / 200 GB floor in plain words, CPU default, GPU only an offer; family PIN at setup (stdin only → scrypt hash in the library); family CA/HTTPS; mandatory backup on a different drive that is not also a photo source; recent-first 2,000-photo first sweep with a counting sky, archive + screening + tagging overnight; start on boot (systemd user unit, never clobbers a hand-made one; Windows least-privilege logon task + private-profile firewall rule); macOS target dropped.
- **Found by E2E, fixed:** (1) new installs' wall stayed empty — no NSFW screener in the bundle; now ships the ONNX export (87 MB, sha256-pinned; parity vs original 52/52 verdicts, max Δ 0.0035); (2) stray `MEMORYVAULT_*` env could redirect PIN/keys — backend calls now start from a clean env; (3) USB drives under `/run/media` were hidden — lsblk-based detection; (4) public `config.py` silently missed new settings — sync-public now fails on any missing setting; (5) unwritable library → raw traceback — now a sentence.
- Tests: installer 35 node tests, 23+ mutations caught; engine 83. E2E: real backend binary 12/12 (install → CPU screening → systemd → wall → PIN over HTTPS → restart-on-kill); real packaged Electron app 16/16 (no downloads made).
- **Open:** Windows build + install on the home server's Windows partition (needs a scheduled window — takes the rig offline); hardware shopping-list page URL is a placeholder.

### CP3 — formats + bursts, 2026-09-23 (constellation#6)
- `formats.py`: HEIC/HEIF → JPEG rendition at ingest (original kept byte-for-byte in `originals/`, rendition in `renditions/`, `photos.original_path`); EXIF read from the original; RAW (12 extensions) → `archive/raw/`, `status='archived'`, never selected by any stage or page; files over `MAX_FILE_BYTES` (4 GB) skipped **before hashing** with a plain message.
- `bursts.py` + `mvault bursts`: time window 3 s AND pHash ≤ 10 from the first frame; keep sharpest (Laplacian variance); others `status='parked'`, `parked_by` keeper — rows, files, thumbnails kept; `--release ID` brings one back and it is never re-parked. Runs after ingest, before screening (first sweep + overnight).
- Tests: engine 83 → **102**. 13 mutations; 3 initially survived (EXIF source, orientation) → EXIF-source test added (now caught); orientation transpose is belt-and-braces because pillow-heif already applies HEIF rotation on decode — documented, behaviour-neutral with this decoder.
- E2E on the real bundled binary: mixed dump (8 HEIC w/ GPS, 6-frame burst, CR3, oversized .mov, 5 JPEG) → wall 14, parked 5 with frame 3 kept, RAW archived, oversized skipped with message, HEIC served as JPEG — **15/15**.
- Not done in CP3: "storage math" UI already shipped in CP2; Live Photo *video* half continues to be skipped (existing behaviour).

### Startup truthfulness repair (AMS task #130), 2026-09-25
The installer could claim startup and background work it had not verified. Those claims are now closed:
- `StartLimitIntervalSec=0` was emitted in `[Service]`, where systemd **ignores** it (confirmed: `systemd-analyze verify` prints "Unknown key … in section [Service]"). Now a `[Unit]` key, where it belongs, so the restart-forever promise is real.
- Linux: the unit is now **verified with `systemd-analyze verify` before enable**; a silent verify is the only "yes" (verify exits 0 even while warning, so the gate reads stderr, not the exit code). A warned unit fails the install instead of shipping broken.
- Windows startup is transactional: the installer snapshots any existing task, launcher/XML, and firewall rule; only exact task XML, the exact documented not-found result, and an exact firewall-rule listing are accepted as known state. Access denial, transport failure, malformed output, and locale-ambiguous output abort before any write. `/Create`, `/Run`, firewall, or product-readiness failure restores the prior resources or removes only resources created by the failed attempt. Cleanup continues after individual restore/delete failures and reports every failure.
- Wizard startup claims require the exact Constellation `/wall` readiness contract (200 + an exact `text/html` media type with optional valid parameters + Constellation title), not a prefix such as `text/html-not-really`, a combined media type, merely an HTTP response, or a successful process/task launch. Linux now reports a verified start, refuses unknown systemd discovery before writing, and preserves explicit enabled/disabled and active/inactive state; login-only startup is distinct from boot startup when `loginctl enable-linger` fails.
- Background copy is capability-gated: a detached spawn is reported as session-only and cannot promise unattended overnight completion. Tests: installer **78/78** node tests, including one-deadline `/wall` response handling, exact media-type parsing, aborted/oversized streams, fail-closed Linux and Windows discovery, exact Linux state restoration with aggregated failures, atomic Windows file-write rollback, unrelated 200/302/404 listeners, timeouts/throws, prior-vs-new resource rollback, cleanup failures, and copy branches. Live `systemd-analyze verify` remains silent for the shipped unit.
