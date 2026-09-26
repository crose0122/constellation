# Constellation v2 — Founder Interview Kit

**Created:** 2026-09-20 · **Owner:** the reviewer · **Feeds:** the next version of `CONSTELLATION-APP-SPEC.md` in this folder.

**the founder's ask (2026-09-20):** "…the installation process should be user friendly and cover all the steps needed to install, locate images, sync with phones or other photo libraries, NSFW vault setup, android and Apple companion app for displaying constellation. And any other parts I'm not thinking of."

**Product stance (the founder, 2026-09-20):** Constellation is a standalone product with its own user base — this spec work is written for Constellation, not from the the household system perspective. (the founder also wants the reviewer's overall coding perspective expanded beyond the household system to any software we build — soul update pending.)

---

## How we run this interview

- One question at a time, plain language, over Telegram. Questions are numbered (A1, B3, …) so we can jump around without losing our place.
- Every question ends in a decision that changes the v2 spec. If an answer wouldn't change what we build, it goes to the parking lot instead.
- Say "skip" to park a question. Answer with "A/B/C" or just talk — voice notes are fine; I'll record the decision.
- Each answer gets recorded under its question, dated.
- **Nothing gets built from this interview until the assembled v2 spec is approved by both founders** (the co-founder's non-negotiable: full spec before code).
- When we're done: I synthesize `CONSTELLATION-APP-SPEC-V2.md` + a phased build plan, present for sign-off.

## Ground truth today (2026-09-20) — what v2 builds on

- Product surfaces: constellation sphere (Explore), gallery wall, Memories slideshow, grid browse, curation/people/progress pages. LAN-only, no cloud, no telemetry.
- Production backend: photo-vault host **<lan-ip>** — Caddy HTTPS front + `memoryvault-constellation.service` on :8484; crash-recovery verified 2026-08-10 (real HTTP 200 acceptance).
- the home server runs the dedicated desktop display app (systemd user unit) pointed at .80.
- **Android shipped:** Kotlin kiosk/screensaver app (DreamService + leanback entry), sideload install, server address as build property.
- **Installer v1 shipped (Electron):** Welcome → system scan (GPU/VRAM/CPU/drives/likely photo folders) → storage pick → Ollama + vision model download → first sweep (foreground init/discover/ingest/curate; background model stages detached) → finish with LAN address.
- Library: ~22k photos in the pipeline; LUKS vault exists as the private-content store; vault content never enters `photos.db` by construction.
- Still-open v1 questions this interview should close: naming ("Constellation" vs "Memories"), the household system integration, curation controls, voice.

## Progress tracker

| Part | Topic | Questions | Status |
|---|---|---|---|
| A | Install & first run | A1–A7 | **COMPLETE — 7/7 ✓** |
| B | Finding & importing photos | B1–B6 | **COMPLETE — 6/6 ✓** |
| C | Phone & cloud sync | C1–C6 | **COMPLETE — 6/6 ✓** |
| D | Private vault (NSFW) | D1–D5 | **COMPLETE — 5/5 ✓** |
| E | Companion apps (Android + Apple) | E1–E6 | **COMPLETE — 6/6 ✓** |
| F | Gaps the founder didn't name | F1–F10 | **COMPLETE — 10/10 ✓** |
| G | Ship shape | G1–G3 | **COMPLETE — G1 ✓ G3 drafted ✓ (G2: 1 beta family exists, identity TBD)** |

**Interview complete 2026-09-20 (37/38 decisions). Synthesis: `CONSTELLATION-APP-SPEC-V2.md` (DRAFT) — awaiting the founder review → the co-founder sign-off.**

---

## Part A — Install & first run

**A1 — Who is "user friendly" actually for?**
Every default (wording, hand-holding, error recovery) flows from this.
- A. Tech-comfortable setter-upper (you + beta families). Wizard assumes patience; ships fastest. Downside: a true novice still hits walls we never tested.
- B. True non-technical parent. Zero terminal ever, every error auto-recovers. Downside: longest build, many more code paths to test.
- C. Both tiers, one wizard — simple path fully hand-held, "Show details" pane for us. **Answer:** **C — one wizard, both tiers** (2026-09-20). Hand-held simple path for parents, "Show details" pane for techy users.

**A2 — Which machines must the installer run on?**
Today: Linux only (AppImage). Real families own Windows laptops and Macs.
- A. Linux only for v2. Downside: cuts most real families.
- B. Linux + Windows. Downside: Windows packaging + service story is real work. **← CHOSEN**
- C. Linux + Windows + macOS. Downside: three packaging paths, macOS notarization.

**Answer:** **B — Linux + Windows** (2026-09-20, corrected from "Linux only" same evening). Windows needs its own auto-start story (tray app / scheduled task, no systemd) and a bigger test matrix. macOS revisited when real customers demand it; display apps handled separately in Part E.

**A3 — What hardware is "the box"?**
- A. Any spare computer with ≥8 GB RAM and ~200 GB free (matches today: no GPU required — CPU path works; the *display* is the memory-bound part, not the server).
- B. A + a blessed "recommended appliance" shopping list for the pre-installed-hardware future. **← CHOSEN**
- C. NAS-first (Synology/Unraid container). Downside: container life on NAS is its own maintenance burden.
v2 must state the display-vs-server hardware distinction in plain words on the hardware step.

**Answer:** **B — any spare PC + blessed shopping list** (2026-09-20). Min spec: ≥8 GB RAM, ~200 GB free, no GPU required (CPU path works; the display is the memory-bound part). Installer also shows a short recommended-appliance page for families buying new gear.

**A4 — Day 2: updates**
v1 has no update story.
- A. Manual "check for updates" that walks the same wizard. Downside: families stuck on old versions.
- B. Auto-update in background (display client already self-heals). Downside: an update can break a wall display overnight.
- C. Auto-update inside a chosen maintenance window. Downside: more settings. **← CHOSEN**

**Answer:** **C — auto-update in a maintenance window** (2026-09-20). Default window 3–5 AM, configurable. Updates happen predictably; the wall is never mid-slideshow when something restarts. Manual "check for updates" stays as a button too.

**A5 — Backup, move, uninstall**
Does v2 ship: backup/restore of library + database to an external drive, a "move to a new box" migration, and a clean uninstall? The 4TB external drive exists — is it the blessed backup target? This becomes a data-safety sibling section to §10 resilience.
- A. Full data-safety kit in v2 (backup/restore + migration + clean uninstall). Biggest extra build.
- B. Backup/restore only in v2; migration + uninstall polish in v2.1. **← CHOSEN**
- C. Park it all. Downside: first dead drive = years of photos gone.

**Answer:** **B — backup/restore in v2** (2026-09-20). 4TB external drive is the blessed first backup target. "Move to a new box" migration and polished uninstall deferred to v2.1.

**A6 — Who can reach the server? (LAN security + HTTPS)**
Today: plain HTTP on LAN, Caddy HTTPS on .80, **no login anywhere**.
- A. Keep it open, no login. Downside: any guest phone on Wi-Fi can browse every family photo.
- B. One shared family PIN on private surfaces (curation, people, vault tools, sync settings); display surfaces stay open. **← CHOSEN**
- C. Per-person accounts. Downside: heavy; nobody logs into a photo frame.

**Answer:** **B — family PIN on private surfaces + HTTPS-everywhere as installer default** (2026-09-20). Wall/Memories/ambient stay open (a picture frame never asks for a password); curation, people, vault tools, and sync settings sit behind one family PIN. Installer sets up HTTPS by default so the PIN and photos never cross Wi-Fi in the clear.

**A7 — The first fifteen minutes (first-run delight)**
Define the empty state (stars waiting), plain-word sweep progress ("reading 4,213 photos…"), and the moment the first neurons light up. Does the wizard's finish step double as the family's first "wow"?
- A. The patient sky — stars appear as photos are read, count-up progress, neurons light one by one. **← CHOSEN**
- B. Instant gallery first, stars second.
- C. Guided first tour.

**Answer:** **A — the patient sky** (2026-09-20). Stars appear as photos are read in with plain-word count-up progress; neurons light one by one; the map is alive by breakfast. Calm, magical, honest about the wait.

---

## Part B — Finding & importing photos

**B1 — Where do the photos actually live? (current-state inventory)**
Honest inventory of the house: laptops, phones, external drives, SD cards, cloud accounts, WhatsApp/Signal media folders, school photos, videos. Then ideal state: what should be the one place photos land?

**Answer:** **LAN-wide stance** (2026-09-20): any connected computer on the LAN could be storing photos, and any phone on the network is a potential auto-sync target. The installer's discovery step is therefore a network inventory, not a local-folder picker: find machines with photo folders, find phones advertising themselves, let the user choose what to pull from where. No family's layout is hardcoded.

**B2 — How aggressive is auto-discovery?**
- A. Suggest only — wizard proposes likely folders (Pictures, Photos, mounted drives), human confirms each. Safe; more clicking.
- B. LAN-wide helper app: tiny read-only helper on each computer reports photo folders and streams files; wizard shows every machine as a card, user picks per machine/folder; consent once per machine. **← CHOSEN**
- C. Whole-drive scan / deep LAN crawl with ignore rules. Most complete; slow and scary.

**Answer:** **B — LAN-wide helper app** (2026-09-20). Each computer runs a tiny Constellation helper (installed once, read-only streaming on request, consent granted once on that machine). The installer's discovery step shows every machine on the LAN as a card — "the other parent's laptop — 3 photo folders found" — and the user chooses per machine, per folder. Non-negotiable stands: sources are read-only; Constellation copies, never moves or deletes originals.

**B3 — One-time import or living sync?**
A one-time harvest of every old drive, an ongoing trickle from phones, or both (import for archives, sync for new photos)? This decision drives the whole sync architecture.

**Answer:** **C — both, split by job** (2026-09-20). Two engines, one pipeline:
- **Import engine** — big archives (external drives, helper-machine folders, USB): chunked, resumable, honest time estimates.
- **Sync engine** — the ongoing trickle (phones now, helper folders optionally): small, frequent, automatic.

**B4 — Duplicates and near-duplicates**
Exact-dup hashing is easy. Burst policy is the real call:
- A. Keep everything, group visually for later culling.
- B. Auto-keep best-of-burst, park the rest (recoverable, never deleted). **← CHOSEN**
- C. Ask during install. Downside: nobody can answer this well at install time.

**Answer:** **B — auto-keep best-of-burst, park the rest** (2026-09-20). During import, near-duplicate groups get picked over: sharpest photo kept, the rest parked — out of the stream, recoverable, never deleted. Library reads clean immediately. Exact duplicates: hash match, keep one.

**B5 — Videos and heavy formats**
Max file size? HEIC / Live Photos handling? RAW files archived or ignored? 4K video storage math — drives fill faster than anyone plans.

**Answer:** **All four defaults confirmed** (2026-09-20):
- **4 GB per-file cap**, plain-language warning when exceeded.
- **HEIC / Live Photos → transcode to JPEG at ingest** (originals untouched on source; archive HEIC when trivial).
- **RAW files (CR3/ARW/NEF): archive-only** — recognized, stored, never shown in the family stream.
- **Storage math shown at the storage step** ("your library is X GB; 1 hour of phone 4K ≈ 45 GB").

**B6 — Big libraries, honest time**
~22k photos took real hours. Installer copy must set expectations in plain words ("first sweep ~40 minutes; full AI tagging runs overnight"). Does import need chunking?

**Answer:** **B — recent-first cap** (2026-09-20). First sweep brings the most recent ~2,000 photos so the app opens in minutes with a living sky; the older archive fills in overnight in the background with honest "catching up on 2009–2025" progress. Chunking/resumability was already settled in B3.

---

## Part C — Phone & cloud sync

**C1 — Which phones and cloud clouds exist?**
iPhones (whose?), Androids (whose?), iCloud library? Google Photos? Amazon Photos? "Other photo libraries" — Apple Photos on a Mac? Lightroom? NAS photo apps?

**Answer:** **All of the above are real** (2026-09-20): the house is a combination — iPhone and Android phones, plus cloud libraries (iCloud/Google Photos) in the mix. v2 therefore needs phone sync connectors for both platforms AND cloud-account pull, not just one path. The sync engine (B3) is the backbone; Part E's apps and this part's connectors both hang off it.

**C2 — The iPhone path (pick one, own the downside)**
- A. Companion app auto-uploads camera roll when home on Wi-Fi. Clean and direct. Downside: iOS background limits — app must be opened periodically; needs App Store/TestFlight presence. **← CHOSEN**
- B. An always-on Mac/PC mirrors Apple Photos and feeds the server. No phone app needed. Downside: requires a Mac in the loop.
- C. Manual "add photos" from the app. Zero magic; abandoned by week three.

**Answer:** **A — companion app auto-uploads on Wi-Fi** (2026-09-20). Aggressive upload while home on Wi-Fi, top-up catch-up when the app opens, Photos-library permission so it sees everything. Honest downside owned: iOS bursts uploads around app opens rather than a perfectly continuous stream. App-distribution decision deferred to E4.

**C3 — The Android path**
- A. Same companion app auto-upload (Android is friendlier — background sync works properly). **← CHOSEN**
- B. Folder sync (Syncthing-style) of DCIM into a staging folder. No app store needed; more moving parts.
- C. Manual pick from app.

**Answer:** **A — same companion app, auto-upload on Wi-Fi** (2026-09-20). One companion app for both platforms with a shared sync core; Android permits true continuous background sync with a one-time permission. The Android side can reuse the existing Kotlin app's base rather than starting from scratch.

**C4 — Deletes and edits — the policy question**
If a photo is deleted on the phone, does it delete on the server?
- A. Additive-only: server never deletes. Safest; junk accumulates (needs B4's cull flow).
- B. Two-way mirror. Risky: one accidental phone wipe nukes the family archive.
- C. Trash-with-delay: phone deletes → 30-day recycle bin on the server. Middle ground; more UI.

**Answer:** **Backup semantics** (2026-09-20, the founder's steer: "part of the theory here is this becomes your photo backup, and you can remove photos from your phone to save space"):
- The server is the **backup of record** — phone deletes never remove server copies; the photo stays and stays visible.
- Junk control is a deliberate adult action (B4's parked/cull machinery behind the family PIN), never a side effect of a phone wipe.
- **"Free up space" flow in the companion app**: "1,214 photos safely on Constellation — tap to free 8.2 GB." Phone storage pressure becomes a reason to love the server.
- Edits arrive as new versions; originals kept.
- Consequence: the server becomes the only copy for freed-space photos → A5 backup/restore is **mandatory**, not optional. The box is the family's photo safe.

**C5 — Away from home**
Wi-Fi-only sync (private, simple) vs secure remote access (Tailscale-style). Recommendation: Wi-Fi-only for v2, remote as stretch.

**Answer:** **A — Wi-Fi-only sync** (2026-09-20). Phones upload when home on the family Wi-Fi; photos taken out reach the server when you walk in the door. Nothing exposed to the internet; remote sync not built in v2.

**C6 — What lands where (consent & privacy)**
Whose camera roll flows into the shared family library automatically? A 12-year-old's camera roll on a family server raises real privacy and trust questions.
- A. Adults full-auto; kids' devices opt-in per kid, upload log visible to parents. **← CHOSEN**
- B. Everyone full-auto. Simple; trust-presuming.
- C. Everyone manual-add. Safe; dies of friction.
Also: do kids get a private shelf the way adults get the vault?

**Answer:** **A — adults full-auto, kids opt-in, kids get a private shelf** (2026-09-20). Adults' camera rolls flow in automatically. A kid's device joins only if the kid turns it on; their photos land on their own private shelf (visible to them alone) with an explicit "share to family wall" choice per photo. Not silent surveillance. Upload log visible to parents for opted-in devices.

**Addendum (2026-09-20, the founder's caveat):** **Individual displays per device.** Constellation is a family of displays, each with its own identity: the living-room display shows the family library; a kid's room gets their OWN display showing their own shelf — their photos, their sky, their wall, not the family feed pushed into their room. "Add to family" is the kid's per-photo choice; they opt in to exactly which pictures are family-shared. (Assumption flagged: unpublishing a shared photo later is reversible — shelf is theirs. Cross-ref: this shapes Part E display-identity design.)

---

## Part D — The private vault (NSFW)

**D1 — What is the vault for, in the family's own words?**
Private adult photos? Sensitive documents? Surprise-planning shots (gifts, parties)? Medical? Scope it before building anything.

**Answer:** **Private adult photos; per-user vaults; auto-routing** (2026-09-20). The vault's primary job is private adult photos. Each user gets their OWN private vault (not one shared family vault); anything private can be shelved there, but adult photos should be AUTO-ADDED — screening detects and routes them into the owner's vault at ingest, so they never sit visible in family streams awaiting review. Consequences: (1) the review queue becomes a RELEASE flow for false positives, not a confirm flow; (2) per-user vaults imply per-user unlock (D4 revisits key custody for this); (3) D5's escalation rule matters more — suspected minor-harm content never quietly vaults, it escalates.

**D2 — When is content routed to the vault?**
- A. At install: user picks folders that are vault-only from day one; nothing auto-routes. Predictable.
- B. Automatic screening at ingest: local NSFW classifier flags candidates → human review → vault on confirm. Magic; false positives need a trusted review flow.
- C. Both: folder routing at install + optional screening when importing old archives.

**Answer:** **B — two doors: auto-routing + folder declaration** (2026-09-20). Per D1, adult photos AUTO-route into the owner's vault at ingest (classifier-driven, never sitting visible awaiting confirm — the review queue is a release-false-positives flow). Alongside that, adults can mark whole folders vault-only at import — everything in them skips family streams entirely, no classifier involved. No one-time re-screening of the existing library in v2 (C's cost declined for now).

**D3 — The review queue (false-positive safety)**
If screening exists: flagged items **never** auto-hide without human confirm. Adults only. On which device? How does the queue itself stay hidden from kids' eyes (ties to A6 auth)? A wrong flag must never shame anyone.

**Answer:** **Release flow confirmed** (2026-09-20). Queue lives behind the family PIN (A6), adults only, reachable from the phone app or a browser; each flag shows whose photo it is so releases route to the right vault owner. False positives release in one tap — no notifications to anyone, a wrong flag never shames. Accepted failure mode: a mis-flagged innocent photo is briefly not on the wall until released (the alternative — confirm-before-vault — fails D1's auto-add requirement).

**D4 — The unlock ceremony & key custody**
LUKS passphrase: who holds it (Vaultwarden?), and what happens if forgotten — **unrecoverable, said out loud during setup, checkbox required**. The installer must make the lost-key trade impossible to miss.

**Answer:** **B — per-user vault secrets, optional sharing at setup** (2026-09-20, the founder: "but can opt in to share vault during setup"). Each adult holds their own vault secret; nobody opens someone else's vault BY DEFAULT — at setup, each adult may opt in to share/link their vault with the other. LUKS recovery key is generated once, shown once, stored in Vaultwarden, with the spoken "lost = unrecoverable" ceremony and a required checkbox in the installer. The vault is never openable with just the family PIN.

**D5 — The guarantee, extended (+ the hard case)**
Today's rule: vault content never enters `photos.db` by construction. v2 extends the same wall to everything new: thumbnails, face clusters, Memories graph, placards, search index, backups, exports. Confirm as a hard architectural invariant with a test.
The hard case: if screening ever flags content suggesting a minor is being harmed, the system does not quietly bury it — founder-approved escalation policy (alert both parents with full context; legal review drafts the written policy). Honest downside: false accusations inside a family — which is exactly why the policy is written down in advance, not improvised.

**Answer:** **Both confirmed** (2026-09-20).
- **The wall, extended:** vault content never enters ANY derivative surface — thumbnails, face clusters, Memories graph, placards, search index, backups, exports. Enforced in code, proven by an automated test (not a promise).
- **The hard case:** classifier flags suggesting a minor is being harmed NEVER quietly vault — both parents are alerted immediately with full context. The escalation policy is written into the spec now (legal review drafts exact text before the classifier ships). Honest downside owned: a false accusation inside a family is itself harmful — which is why the rule is narrow, written in advance, and not an AI judgment call in the moment.

---

## Part E — Companion apps (Android + Apple)

**E1 — What is each app for?**
Display only (wall/TV/screensaver)? Browse in hand? Upload camera roll? Control what the wall shows? Rank the jobs — this sizes the whole build.

**Answer:** **All four jobs, one app, roles by device** (2026-09-20):
- **Display** — each room runs its own Constellation: living-room wall, TV, kid rooms on their own shelves (C6 addendum made this first-class).
- **Browse** — explore/search the library in hand.
- **Upload** — the camera-roll backup engine (C2/C3).
- **Control** — steer any display from a phone ("show Christmas 2019 on the wall").

**E2 — Android scope for v2**
Kiosk/screensaver exists. Expand to browse+upload (one app, role chosen per device), or keep display-only and let the PWA handle in-hand browsing?

**Answer:** **A — thin native shell** (2026-09-20). Native keeps exactly what needs native: DreamService screensaver, background upload service, keep-screen-on kiosk. Browse + Control render through the existing web UI in the WebView — one web codebase powers everything, least ongoing maintenance. The app grows from display-only into the four-role companion (E1), with the role chosen per device.

**E3 — Apple surfaces — which matter?**
iPhone (upload/browse)? iPad (Explore in hand / shelf display)? Apple TV (Ambient/Memories)? Reality check: tvOS has no screensaver equivalent — options are a tvOS app, AirPlay, or keeping a cheap Android box on the TV. Which Apple devices do the family and beta families actually own?

**Answer:** **iPhone native; iPad + Apple TV via PWA/AirPlay for v2** (2026-09-20). The iPhone companion is the Apple build; iPads use the web app in hand; Apple TV is the weakest Apple story in v2 — covered by AirPlay from a phone or a cheap Android TV box driving the TV (today's pattern). No native iPad or tvOS apps in v2.

**E4 — Distribution reality (Apple)**
- A. App Store (public). Apple Developer $99/yr + review; LAN-only apps are allowed but review can be finicky.
- B. TestFlight (private beta). Same $99/yr, softer review, builds expire after 90 days (reinstall churn).
- C. PWA-first for Apple (install from Safari). Cheapest; iOS PWA quirks; no background upload.

**Answer:** **B — TestFlight first, App Store once stable** (2026-09-20). Apple Developer account required either way ($99/yr). Beta covers this house + early families first; public store page follows once the app is proven. Downside owned during beta: builds expire every 90 days and must be reinstalled.

**E5 — Sync status feedback**
When the other parent's phone finishes uploading 214 photos, does anyone hear about it? Default recommendation given the founder's notification sensitivity: silent, progress page only, opt-in notify.

**Answer:** **Silent by default** (2026-09-20). Sync progress lives in the app's sync page; one optional daily digest notification, OFF by default; no per-upload pings, ever.

**E6 — Maintenance weight**
Two native codebases (Kotlin + Swift) is a real ongoing cost. Strategy options: native display apps + shared PWA for browse/upload; PWA-everywhere-except-TV; full native on both. Pick the downside the family can live with.

**Answer:** **Middle — start minimum, promote later** (2026-09-20). Ship two thin native shells (Kotlin + Swift) wrapping one shared web app; once real usage shows which browse screens matter most, promote those to native on each platform. Owned downside: the promoted screens become a second surface to keep current.

---

## Part F — Parts the founder didn't name (confirm or park each)

- **F1 — Search:** "find that photo" — text search over captions/places/people. In v2 or park?
- **F2 — People & faces:** clustering maturity, naming, who-appears-where. In v2?
- **F3 — Sharing outside the house:** grandparents viewing a curated slice? Biggest privacy line in the product. Default: not in v2.
- **F4 — Backup & disaster recovery:** overlaps A5 — decide once, place it in the right section.
- **F5 — Storage growth & lifecycle:** 10-year plan — dedupe, cold tiers, drive-full alert at 70%.
- **F6 — Health & self-repair:** disk alerts, db integrity, AMS watching Constellation. In scope?
- **F7 — Integration boundary:** v2 ships standalone (the founder, 2026-09-20). Any future hooks into the household system or other software are opt-in extras, never assumptions. Confirm and park.
- **F8 — Print products:** annual family photo book from the memory graph. Park or v2-later?
- **F9 — Voice:** "show me Alex's graduation." v1 parked until face queries are reliable. Still parked?
- **F10 — The name:** "Constellation" vs "Memories" for the shelf product. Open since 2026-07-28. Decide in v2.

**Answers (2026-09-20, batch confirmed):**
- **F1 — Search: IN v2.** Text search over captions/places/people is half of Browse (E1); it ships in v2.
- **F2 — People & faces: continue.** Face management shipped in v1 (/people, /curation); v2 keeps and matures it, no new scope.
- **F3 — Sharing outside the house: OUT of v2.** Biggest privacy line in the product; grandparents access waits for a dedicated design pass (v3 candidate). Nothing leaves the LAN in v2.
- **F4 — Backup & disaster recovery: decided in A5.** Backup/restore ships in v2 (external drive blessed target); migration/uninstall in v2.1. No duplicate section.
- **F5 — Storage growth & lifecycle: basic set IN v2.** Drive-full alert at 70%, storage math at the storage step (B5); dedupe/cold tiers parked beyond v2.
- **F6 — Health & self-repair: basic set IN v2.** Disk alerts, db integrity checks, basic pipeline self-repair; external watch integration parked (standalone stance).
- **F7 — Integration boundary: CONFIRMED standalone.** Future hooks into the household system or other software are opt-in extras, never assumptions.
- **F8 — Print products: PARKED** (v2.1+). Annual photo book from the memory graph is a lovely later idea.
- **F9 — Voice: PARKED** (v2.1+). "Show me Alex's graduation" waits until face queries are reliable.
- **F10 — The name: DECIDED — Constellation everywhere.** One name across product, app, services, and repo; least churn. (Open since 2026-07-28, now closed.)

---

## Part G — Ship shape

**G1 — The v2 headline:** if we land exactly one thing — installer story, phone sync, or the Apple app — which one?

**Answer:** **Phone sync is the headline** (2026-09-20): "your photos are automatically safe at home and alive on your walls." The installer and companion apps are its delivery vehicle; backup is the soul of the product after C4.

**G2 — Beta families:** do they exist yet beyond this house? If yes, whose house is first?

**Answer:** _asked 2026-09-20 (late), awaiting the founder._

**G3 — Definition of done:** for each v2 feature, one family-facing acceptance sentence ("the other parent's phone uploads overnight without anyone touching it").

**Draft (the reviewer, for the founder's approval — one sentence per feature):**
1. **Installer:** "A parent installs Constellation on a spare PC in one evening, without a terminal, and the family sees their photos on the wall the same night."
2. **First run:** "The sky fills with recent photos within minutes of install finishing, and the older archive completes overnight by itself."
3. **Discovery & import:** "The installer shows every computer on the home network as a card, and the family picks folders per machine — originals are never touched."
4. **Phone sync (headline):** "Photos taken on any family phone appear on the wall by the time you're home, without anyone pressing anything."
5. **Backup semantics:** "Deleting photos off a phone to free space never loses them — they're already safe at home."
6. **Vault:** "Adult photos route into the owner's private vault automatically, the owner's secret is the only key, and a wrongly flagged photo is one tap to release."
7. **Vault guarantee:** "No vault photo can appear in any thumbnail, face cluster, memory, placard, search result, or backup — and a test proves it."
8. **Companion apps:** "One app: a kid's room runs their own shelf, a phone uploads and browses, and any display can be steered from any phone."
9. **Updates:** "Constellation updates itself between 3 and 5 AM and the wall is never interrupted mid-memory."
10. **Health:** "If a drive is 70% full or the library hits a problem, an adult hears about it in plain words before it becomes a crisis."

---

## Parking lot

(nothing parked yet)

---

## After the interview — synthesis plan

Assemble `CONSTELLATION-APP-SPEC-V2.md`: decisions record → install & first-run spec → ingest & sync engine → private vault spec → companion apps spec → acceptance tests per feature → phased build plan. Both founders sign off before any code (the co-founder's non-negotiable).