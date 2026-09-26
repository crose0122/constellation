# Constellation — Product Specification V2 (DRAFT)

**Date:** 2026-09-20 · **Status:** APPROVED 2026-09-23 by the founder (AMS #114). the co-founder sign-off waived by the founder for this standalone project. Canonical V2 spec; supersedes `CONSTELLATION-APP-SPEC.md` (v1 record) for V2 work. Execution: `CONSTELLATION-V2-BUILD-PLAN.md`.

**Headline (G1):** *Your photos are automatically safe at home and alive on your walls.* Phone sync is the feature; backup is the soul; the installer and companion apps are the delivery vehicle.

**Stance:** Constellation is a standalone product with its own user base (the founder, 2026-09-20). Written for families, not as a the household system surface.

---

## 0. Decisions record (interview, 2026-09-20)

| # | Decision |
|---|---|
| A1 | One installer wizard, both tiers: hand-held simple path + "Show details" pane |
| A2 | **Linux + Windows** (corrected from Linux-only same evening). macOS deferred |
| A3 | Any spare PC (≥8 GB RAM, ~200 GB free, no GPU needed) + blessed shopping list page |
| A4 | Auto-update in a maintenance window, default 3–5 AM, configurable; manual check button stays |
| A5 | Backup/restore ships in v2 (4TB external drive = blessed target); migration + uninstall polish → v2.1 |
| A6 | Family PIN gates private surfaces (curation/people/vault/sync settings); display surfaces stay open; **HTTPS-everywhere is the installer default** |
| A7 | "The patient sky" first run: stars appear as photos are read, count-up progress, honest about the wait |
| B1 | LAN-wide stance: any computer may hold photos, any phone may auto-sync; discovery = network inventory |
| B2 | Tiny **read-only helper app** per computer reports folders and streams files on request; consent once per machine |
| B3 | Two engines, one pipeline: **import** (chunked, resumable) + **sync** (small, frequent, automatic) |
| B4 | Exact-dup hash → keep one; bursts → auto-keep sharpest, park the rest (recoverable, never deleted) |
| B5 | 4 GB/file cap with plain warning; HEIC/Live Photos → JPEG at ingest (originals untouched); RAW archive-only; storage math shown at storage step |
| B6 | Recent-first first sweep (~2,000 newest) so the app opens in minutes; archive backfills overnight |
| C1 | Phones: iPhone + Android; clouds (iCloud/Google Photos) real. Connectors for both platforms + cloud pull |
| C2 | iPhone: companion app auto-uploads camera roll on Wi-Fi; top-ups on app open (iOS burst behavior owned) |
| C3 | Android: same companion app, shared sync core; true background sync with one-time permission |
| C4 | **Backup semantics:** phone deletes never remove server copies; "free up space" flow in-app; edits = new versions, originals kept; A5 backup becomes mandatory |
| C5 | Wi-Fi-only sync in v2; nothing exposed to the internet |
| C6 | Adults full-auto; kids opt-in per kid; **kids get a private shelf**; per-photo "share to family" choice. **Addendum: individual displays per device** — a kid's room runs THEIR OWN Constellation over their own shelf |
| D1 | Vault = per-user private vaults for adult photos; **auto-routing at ingest** (review queue = release-false-positives flow) |
| D2 | Two doors: auto-routing + folder declaration at import; no re-screen of existing library |
| D3 | Release queue behind family PIN, adults only, one-tap release, zero notifications; accepted: mis-flagged photo briefly off-wall |
| D4 | Per-user vault secrets; optional vault sharing at setup; LUKS key shown once → Vaultwarden; spoken "lost = unrecoverable" + checkbox |
| D5 | Vault wall extends to ALL derivatives (thumbnails, faces, Memories, placards, search, backups, exports) with an automated proving test; minor-harm flags escalate to both parents — policy written now, legal review drafts exact text before classifier ships |
| E1 | One companion app, four roles by device: Display / Browse / Upload / Control |
| E2 | Android: thin native shell (screensaver, background upload, kiosk native; Browse+Control in WebView) |
| E3 | Apple: iPhone native; iPad via PWA; Apple TV via AirPlay or Android TV box (weakest Apple story, owned) |
| E4 | TestFlight first → App Store once stable; Apple Developer $99/yr; 90-day beta reinstall churn owned |
| E5 | Sync silent by default; one optional daily digest (off); no per-upload pings, ever |
| E6 | Start minimum-native (two thin shells + one web app); promote hot browse screens to native later as usage proves them |
| F1–F10 | Search IN · faces continue · external sharing OUT · storage alerts IN (70%) · basic self-repair IN · standalone confirmed · print + voice PARKED · **Name: "Constellation" everywhere** |
| G1 | Headline = phone sync |
| G2 | One beta family exists (identity TBD — confirm when beta program starts) |
| G3 | Ten acceptance sentences drafted (§2); approval folds into spec sign-off |

---

## 1. What v2 is

Constellation stays what it is — the family's photo universe: the constellation sphere (Explore), gallery wall, Memories slideshow, grid browse, curation/people pages. LAN-only, no cloud, no telemetry — unchanged and non-negotiable.

v2 makes it **installable by any family and self-sustaining for this one**:

1. **A real installer** (Linux + Windows) that goes from zero to a living sky in one evening.
2. **Phone sync as the headline** — camera rolls flow home over Wi-Fi; the server is the family's photo backup of record.
3. **Per-user private vaults** with automatic adult-photo routing, key ceremony, and the derivative wall.
4. **A family of displays** — every room can run its own Constellation, including kid rooms on their own shelves.
5. **One companion app** (Android + iOS) with four roles: Display, Browse, Upload, Control.

### 2. Definition of done (G3 — ten acceptance sentences, each a build gate)

1. **Installer:** a parent installs Constellation on a spare PC in one evening, without a terminal, and the family sees their photos on the wall the same night.
2. **First run:** the sky fills with recent photos within minutes of install finishing, and the older archive completes overnight by itself.
3. **Discovery & import:** the installer shows every computer on the home network as a card, and the family picks folders per machine — originals are never touched.
4. **Phone sync (headline):** photos taken on any family phone appear on the wall by the time you're home, without anyone pressing anything.
5. **Backup semantics:** deleting photos off a phone to free space never loses them — they're already safe at home.
6. **Vault:** adult photos route into the owner's private vault automatically, the owner's secret is the only key, and a wrongly flagged photo is one tap to release.
7. **Vault guarantee:** no vault photo can appear in any thumbnail, face cluster, memory, placard, search result, or backup — and a test proves it.
8. **Companion apps:** one app: a kid's room runs their own shelf, a phone uploads and browses, and any display can be steered from any phone.
9. **Updates:** Constellation updates itself between 3 and 5 AM and the wall is never interrupted mid-memory.
10. **Health:** if a drive is 70% full or the library hits a problem, an adult hears about it in plain words before it becomes a crisis.

---

## 3. Install & first run (Part A)

### 3.1 The wizard
Six steps, both tiers in one binary: **Welcome → System scan → Storage & sources → Downloads → First sweep → Finish.** Simple path is fully hand-held (plain words, one decision per screen, every default pre-chosen sensibly). "Show details" reveals the technical pane (paths, model choice, log tail) for techy users. No terminal, ever.

### 3.2 Platforms (A2)
- Linux: AppImage + a systemd user-unit story for the server (v1 pattern continues).
- Windows: installer + tray app / scheduled task for auto-start (no systemd). Windows is a first-class v2 target: real test matrix, real packaging effort.
- macOS: deferred until customers demand it.

### 3.3 Hardware (A3)
Minimum: any spare PC with ≥8 GB RAM and ~200 GB free. **No GPU required** — the server's model stages run on CPU; the *display device* is the memory-bound side (measured, v1 §8). The wizard says this in plain words. A blessed "recommended appliance" shopping list page ships alongside for families buying new hardware.

### 3.4 Updates (A4)
Auto-update inside a maintenance window — default **3–5 AM**, configurable. The display client reconnects itself after updates (existing self-heal). A manual "check for updates" button remains.

### 3.5 Data safety (A5)
Backup/restore ships in v2: library + database to an external drive; the 4TB external drive is the blessed first target. Because of C4 (the server becomes the only copy of freed-space photos), backup is **mandatory**, not optional — the wizard walks it. Move-to-new-box migration and polished uninstall → v2.1.

### 3.6 Who can reach what (A6)
- **Open surfaces** (no login): wall, Memories, ambient — a picture frame never asks for a password.
- **Family PIN surfaces:** curation, people, vault tools, sync settings, release queue, health/backup.
- **HTTPS-everywhere is the installer default** (family-CA pattern from v1 §3 Phase A) so PIN and photos never cross Wi-Fi in the clear.

### 3.7 First run (A7 — the patient sky)
Empty state = stars waiting. As the recent-first sweep reads photos in, a plain count-up ("reading 4,213 photos…") runs and neurons light one by one. By breakfast the map is alive. The finish screen doubles as the family's first "wow": it opens the sky and lets it fill.

---

## 4. Discovery & import (Part B)

### 4.1 The helper app (B1+B2)
A tiny, boring, read-only helper runs on each computer with photos worth pulling. It advertises itself on the LAN, reports its photo folders, and streams files only when asked — it never moves, renames, or deletes anything, and it grants consent once, on that machine, in that user's session. The wizard's scan shows every machine as a card: "the other parent's laptop — 3 photo folders found."

### 4.2 Two engines (B3)
- **Import engine** — archives: external drives, helper folders, USB. Chunked, resumable, honest time estimates.
- **Sync engine** — the trickle: phones (§5), optionally helper folders. Small, frequent, automatic.
One pipeline feeds both into ingest.

### 4.3 Formats & culling (B4+B5)
- Exact duplicates: hash match, keep one.
- Bursts: auto-keep the sharpest, **park** the rest (out of streams, recoverable, never deleted).
- 4 GB/file cap with a plain-language warning; HEIC/Live Photos → JPEG at ingest (originals untouched on source; HEIC archived when trivial); RAW (CR3/ARW/NEF) archive-only; storage math shown at the storage step ("1 hour of phone 4K ≈ 45 GB").

### 4.4 First sweep (B6)
Recent-first: ~2,000 newest photos so the app opens minutes after install with a living sky. The older archive backfills overnight in the background with honest progress ("catching up on 2009–2025").

---

## 5. Phone & cloud sync (Part C — the headline)

### 5.1 Upload paths (C1–C3)
One companion app, shared sync core, two platforms:
- **iPhone:** Photos-library permission; aggressive upload while home on Wi-Fi; top-up catch-up on app open (iOS bursts uploads around app opens — owned honestly).
- **Android:** true background sync with a one-time permission; builds on the existing Kotlin app.
- **Cloud libraries (iCloud/Google Photos):** pull connectors — design in the build plan (§12, P3); they feed the same sync engine.

### 5.2 Backup semantics (C4 — the soul)
- The server is the **backup of record**: phone deletes never remove server copies; photos stay and stay visible.
- "Free up space" flow in the app: "1,214 photos safely on Constellation — tap to free 8.2 GB."
- Edits arrive as new versions; originals kept.
- Junk control (B4 parked flow, culls) is a deliberate adult action behind the PIN — never a side effect of a phone wipe.
- Consequence: A5 backup is mandatory (§3.5).

### 5.3 Boundaries (C5–C6)
- Wi-Fi-only in v2; nothing exposed to the internet; remote sync parked.
- **Adults full-auto.** A kid's device syncs only if the kid turns it on. Opted-in kids land on **their own private shelf** — visible to them alone — with a per-photo "share to family" choice. Upload log visible to parents for opted-in devices. Not surveillance; trust with receipts.

---

## 6. A family of displays (C6 addendum + E1 Display/Control)

Every display device picks an identity at setup: **family** (the shared library) or **personal** (a specific person's shelf). A kid's room runs *their own* Constellation — their photos, their sky, their wall — not the family feed. Any display can be **steered** from any phone in the house ("show Christmas 2019 on the wall") — Control rides the same app (E1). Ambient/Memories/wall modes all respect the identity (family mode never mixes shelf content in; personal mode never leaks to family surfaces).

---

## 7. Private vaults (Part D)

### 7.1 Shape (D1)
Per-user private vaults, primary job: adult photos. Anything private can be shelved; **adult photos auto-route** at ingest into the owner's vault — they never sit visible awaiting confirm.

### 7.2 Two doors (D2)
1. **Auto-routing:** classifier-driven at ingest.
2. **Folder declaration:** adults mark whole folders vault-only at import; contents skip family streams entirely, no classifier involved.
No one-time re-screen of the existing library in v2.

### 7.3 Release queue (D3)
Behind the family PIN, adults only, phone app or browser. Each flag shows its owner; release is one tap; zero notifications; a wrong flag never shames. Accepted: a mis-flagged innocent photo is briefly off-wall until released.

### 7.4 Keys (D4)
Per-user vault secrets. Nobody opens someone else's vault by default — **at setup each adult may opt in to share their vault** with the other. LUKS recovery key: generated once, shown once, stored in Vaultwarden, with the spoken "lost = unrecoverable" ceremony and a required checkbox. The family PIN never opens a vault.

### 7.5 The wall & the hard case (D5)
- **The wall:** vault content never enters ANY derivative surface — thumbnails, face clusters, Memories graph, placards, search index, backups, exports. Enforced in code; an automated test proves it continuously.
- **The hard case:** flags suggesting a minor is being harmed never quietly vault — both parents are alerted immediately with full context. The written escalation policy is part of this spec's acceptance gates; **legal review drafts exact text before the classifier ships.** Honest downside owned: a false accusation inside a family is itself harmful — the rule is narrow, pre-written, and never an in-the-moment AI judgment call.

---

## 8. Companion apps (Part E)

- **One app, four roles** (E1): Display / Browse / Upload / Control. Role chosen per device at setup.
- **Android** (E2): thin native shell — DreamService screensaver, background upload service, keep-screen-on kiosk; Browse + Control render the web app in a WebView.
- **Apple** (E3): iPhone native (the build); iPad via PWA; Apple TV via AirPlay or an Android TV box. tvOS native is the weakest Apple story in v2, owned.
- **Distribution** (E4): TestFlight first → App Store once stable. Apple Developer $99/yr; 90-day beta reinstall churn owned.
- **Quietness** (E5): sync silent by default; one optional daily digest, off; no per-upload pings, ever.
- **Codebase strategy** (E6): two thin shells + one web app now; promote the most-used browse screens to native later as usage proves them.

---

## 9. Updates & health (A4, F5, F6)

- Maintenance-window auto-update (§3.4).
- Drive-full alert at **70%**; disk + db integrity checks; basic pipeline self-repair (restart-safe sweeps, resumable stages — v1 §10 patterns generalized).
- Health messages are plain-language, grouped by root cause, and reach an adult before a crisis.

---

## 10. Out of scope for v2

External sharing (grandparents) · print products · voice queries · native iPad/tvOS apps · macOS installer · remote (away-from-home) sync · vault library re-screen · dedupe cold tiers · cloud push (Constellation never uploads family photos to anyone's cloud). Parking lot lives in the interview kit.

---

## 11. Build plan (phases; each ends at its acceptance gate)

| Phase | Content | Gate (from §2) |
|---|---|---|
| P0 — Foundation | Spec sign-off; helper-app protocol design; Windows packaging spike | — |
| P1 — Installer v2 | Wizard both tiers, Linux + Windows, PIN + HTTPS defaults, patient-sky finish | Gates 1, 9 |
| P2 — Import engine | Helper app (Linux+Windows), machine cards, chunked import, formats, bursts, recent-first | Gates 2, 3 |
| P3 — Sync engine + phones | Companion upload (Android first, iOS via TestFlight), backup semantics, free-up-space, kids' shelves, cloud pull design | Gates 4, 5 |
| P4 — Vaults | Per-user vaults, auto-routing, release queue, key ceremony, derivative wall + proving test, escalation policy (legal review) | Gates 6, 7 |
| P5 — Displays & control | Display identities, kid-room mode, control from phone, app roles | Gate 8 |
| P6 — Hardening | Backup/restore (Elements), 70% alerts, self-repair, update window, full regression | Gates 9, 10 |

Beta: this house first (it always was); the one external beta family (G2, identity TBD) installs at the end of P3.

---

## 12. Open items

1. ~~Spec sign-off~~ — DONE 2026-09-23 (the founder; the co-founder gate waived).
2. G2: name the external beta family.
3. legal review: exact escalation-policy text (before P4 classifier ships).
4. E4: Apple Developer account enrollment ($99/yr) before TestFlight build.
5. Commit strategy: interview kit + this spec currently uncommitted (Photo-Project is on `fix/app-window-icon` with an unrelated dirty spec file) — land on a clean `spec/v2` branch at sign-off.

---

## 13. Next version (v2.1) — queued changes

Founder requests captured after V2 approval. Not V2 build gates; they get scheduled in the next version.

### 13.1 Calmer gallery wall on desktop / high-res screens (founder, 2026-09-26)

**Problem:** On a computer or other high-resolution screen, the gallery wall (`/wall`) packs in too many frames (about 12–15 or more). It feels busy and you can't actually look at any one picture or read its placard.

**Cause (as built):** `gridPlan()` in `scripts/memoryvault/constellation/static/wall.html` sizes the grid from screen pixels (`320px` per cell, up to 6 × 5). A 1920×1080 screen gets 6 × 3 cells, or about 16 frames. A 2560×1440 screen gets 6 × 5 cells, or about 28 frames.

**Requirement:**
- Default (non-lite) wall on desktop / high-res displays shows **about 6–7 frames**, not a screen-filling salon. Frames are larger and placards are readable from a normal viewing distance.
- The frame count is set by a target count, not by pixel density. Higher resolution makes frames sharper, not more numerous.
- Keep the 2×2 feature frame(s) for rhythm, counting them toward the 6–7 total.
- Lite / TV-box mode and small screens are unchanged unless they exceed the same cap.
- A URL override (e.g. `?frames=N`) stays available for anyone who wants a denser wall.

**Acceptance:** At 1920×1080 and 2560×1440 in a desktop browser, `/wall` shows 6–7 frames (features included), and every placard is legible without zooming.

**Status:** built 2026-09-26 on `feat/v2-unify`. Screens ≥1100 CSS px wide (not `?lite`) get a 5×2 grid: one 2×2 feature plus 6 singles, 7 frames in all. `?frames=N` asks for about N; `?frames=0` restores the packed salon. Measured at 1920×1080, 2560×1440 and 1366×768: 7 frames, none off-screen. Lite and small screens are unchanged.

### 13.2 Bigger gallery thumbnails (founder, 2026-09-26)

The searchable thumbnail page (`/gallery`) shows each thumbnail **25% larger**: the grid's minimum cell goes from 110px to 138px. **Status:** built 2026-09-26 on `feat/v2-unify`.
