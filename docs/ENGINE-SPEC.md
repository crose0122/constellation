# Memory Vault — System Specification v1.0

**Date:** 2026-07-23
**Status:** Approved direction (resolves issue #1)
**Supersedes:** the *architecture choice* left open between `PLAN.md` and `Hub/Photo-Library-Spec.md`. Both remain useful references; where they conflict with this document, this document wins.

---

## 1. Decisions record (2026-07-23)

| Decision | Choice |
|---|---|
| System of record | **Hybrid**: SQLite (`photos.db`) is the machine index and single source of truth; Obsidian notes are a *generated, disposable* human-facing view |
| Private-content storage | **LUKS encrypted container**, mounted on demand only |
| Screening | **Two-pass**: fast local NSFW classifier on everything → VLM yes/no confirmation on flagged items → manual-review queue for disagreements |
| v1 sweep scope | **Photos first**; videos are inventoried but not screened/tagged until v1.1 (keyframe sampling — designed in now, built next) |
| v1 deployment | **the home server only** (<lan-ip>); mirrored 2-VM infra is fully specified here (§10) but gated on hardware arrival |
| Memory-graph dashboard | **"The Brain"**: custom LAN web view (§9.1), both interactive-explore and ambient-wall modes, photo-thumbnail neurons with relation-colored glowing pathways; prototyped early against sample data in parallel with M1 |
| Deployment (updated 2026-07-23) | Proxmox hosts are available now: a **dedicated VM** hosts the library, DB, and web app; **the home server stays the GPU/inference host** (Ollama + classifier over LAN). M7 is no longer hardware-gated. |
| Backup (resolves issue #2) | **No cloud vendor.** The second Proxmox VM's replica (§10) is the backup. Caveat accepted: both copies are on-site; an off-site encrypted copy can be revisited later. |
| Prototype sample | No curated folder exists — photos are scattered (that's the point of the project). The **discovery sweep gathers the sample**: `mvault` takes a random ~500-photo subset of discovered files for M1.5. |

**Decision reaffirmed (2026-07-23, end of day):** two concurrent sessions recorded
conflicting architecture calls this morning (this SPEC's hybrid vs. PR #5's
"Obsidian-only, SQLite as parking lot"). the founder resolved it: **this SPEC stands** —
the hybrid *is* the combo he wants, and The Brain / web UI is the headline goal.
Consequences:

- PR #5's `consolidate.py` output — the `Photos/YYYY` library on the home server
  (11,386 unique photos from the external-drive and cloud-folder sweeps) — is the
  **primary source feed** for M4 ingest, alongside the nightly discovery crons.
- **the photo server VM** (a VM, Proxmox Test) hosts the library/DB/Brain per §10, and
  additionally serves as the **rsync backup target** for the vault `Photos/` library
  until full ingest supersedes that copy.

Non-negotiable invariants, carried forward from PLAN.md:

1. **Nothing leaves the LAN.** No cloud vision APIs, no hosted moderation APIs, no telemetry.
2. **Originals are never deleted automatically** — only quarantined for explicit human approval.
3. **Screening runs before any tagging/captioning.** No caption, tag, thumbnail, embedding, or filename for private content is ever written to the shared database or vault notes, even transiently.
4. **A screening error is never a verdict.** Infrastructure failure must fail safe (skip + retry queue), never quarantine.

---

## 2. Architecture overview

```
                    ┌──────────────────────────────────────────────┐
 sources            │              the home server (v1)                 │
 ┌──────────┐       │                                              │
 │ Elements │  scan │  ┌─────────┐   ┌──────────┐   ┌───────────┐  │
 │ drive    ├──────►│  │ Ingest  ├──►│ Dedup    ├──►│ Screening │  │
 │ Windows  │       │  │ (hash,  │   │ (SHA256+ │   │ (2-pass)  │  │
 │ drive    │       │  │  EXIF,  │   │  pHash)  │   └─────┬─────┘  │
 │ SD/phone │       │  │  stage) │   └──────────┘     ┌───┴────┐   │
 │ backups  │       │  └─────────┘                    ▼        ▼   │
 └──────────┘       │                             ┌───────┐ ┌────┐ │
                    │                             │ vault │ │Tag │ │
                    │                             │ LUKS  │ │VLM │ │
                    │                             └───────┘ └─┬──┘ │
                    │       ┌────────────────────────────────┘     │
                    │       ▼                                      │
                    │  ┌──────────┐    ┌──────────────────────┐    │
                    │  │photos.db │───►│ generated views:     │    │
                    │  │ (SQLite, │    │  • Obsidian notes    │    │
                    │  │  truth)  │    │  • search / web UI   │    │
                    │  └──────────┘    │  • memory graph      │    │
                    └──────────────────└──────────────────────┘────┘
```

**The contract of the hybrid:** anything a human browses (Obsidian notes, later the web UI) is *derived* from `photos.db` and can be deleted and regenerated at any time with zero data loss. All pipeline state — hashes, tags, dedup groups, screening verdicts, edges — lives only in the DB. The Obsidian vault keeps its Syncthing-to-phone browsing exactly as today, but it stops being a place where state accumulates.

---

## 3. Storage layout (the home server)

```
/srv/data/photo-library/
  staging/                      # ingest copies land here first; source untouched
  originals/<YYYY>/<MM>/<sha256[:8]>-<original-name>   # immutable, stored once
  duplicates/                   # dedup losers, pending one-tap human approval
  thumbnails/                   # generated, disposable
  photos.db                     # SQLite system of record (never contains vault refs)
  vault.img                     # LUKS container (opaque blob at rest)
  errors/                       # nothing stored here; see `errors` table

/srv/data/owner/memory-vault/   # Obsidian vault (existing, Syncthing-synced)
  Photos/<year>/*.md            # generated photo notes
  People/ Occasions/ ... /      # generated index notes
  .schema/tag-schema.json       # tag vocabulary (versioned)
```

Notes:

- **`staging/` decouples reading sources from processing.** Sources (external drives, phone dumps) are read-only inputs; the pipeline never renames, moves, or deletes anything under a source root. Today's behavior of `photo.rename()` on source files is retired.
- **`originals/` naming** uses the SHA256 prefix so identity survives renames and the path is collision-free.
- **The manual-review queue for screening lives *inside* the LUKS container** (`vault:/review/`), not in a plain folder — items there are possibly-explicit by definition. Reviewing requires mounting the vault, which is a deliberate act by design.
- **Nothing about vault contents is recorded in `photos.db`** — not a row, not a hash, not a count-per-file. A single aggregate counter (`vaulted_total`) in `stats` is the only permitted trace.

---

## 4. Database schema (`photos.db`)

SQLite, WAL mode. This is the full v1 schema; later phases only add tables.

```sql
CREATE TABLE photos (
  id            INTEGER PRIMARY KEY,
  sha256        TEXT NOT NULL UNIQUE,
  phash         TEXT,                    -- hex; Hamming matching in code
  width         INTEGER, height INTEGER,
  taken_at      TEXT,                    -- from EXIF, ISO8601, nullable
  camera        TEXT,
  gps_lat REAL, gps_lon REAL,
  place_id      INTEGER REFERENCES places(id),
  media_kind    TEXT NOT NULL DEFAULT 'photo',   -- photo | video (v1.1)
  status        TEXT NOT NULL,           -- staged|screened|tagged|indexed
  screen_score  REAL,                    -- pass-1 classifier score
  library_path  TEXT,                    -- path under originals/
  created_at    TEXT NOT NULL
);

CREATE TABLE files (                     -- every place a photo was ever seen
  id INTEGER PRIMARY KEY,
  photo_id INTEGER REFERENCES photos(id),-- NULL until ingest hashes it
  source_id INTEGER NOT NULL REFERENCES sources(id),
  source_path TEXT NOT NULL,
  size INTEGER, mtime TEXT,
  media_kind TEXT NOT NULL DEFAULT 'photo',
  disposition TEXT NOT NULL,             -- discovered|canonical|duplicate
  discovered_at TEXT NOT NULL,
  UNIQUE(source_id, source_path)
);

CREATE TABLE sources (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,                    -- local|usb|smb|phone-export
  root TEXT NOT NULL UNIQUE,
  description TEXT,
  last_scan_at TEXT
);

CREATE TABLE tags (
  photo_id INTEGER NOT NULL REFERENCES photos(id),
  dimension TEXT NOT NULL,               -- key from tag-schema.json
  value TEXT NOT NULL,
  confidence REAL,
  model_version TEXT NOT NULL,           -- e.g. qwen2.5vl:3b@schema-1.0
  PRIMARY KEY (photo_id, dimension, value)
);

CREATE TABLE duplicate_groups (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,                    -- exact|near
  keeper_photo_id INTEGER NOT NULL REFERENCES photos(id)
);
CREATE TABLE duplicate_members (
  group_id INTEGER NOT NULL REFERENCES duplicate_groups(id),
  file_id  INTEGER NOT NULL REFERENCES files(id),
  decision TEXT NOT NULL DEFAULT 'pending',   -- pending|discard-approved|restored
  PRIMARY KEY (group_id, file_id)
);

CREATE TABLE places (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,                    -- offline reverse-geocoded
  lat REAL, lon REAL
);

CREATE TABLE photo_edges (               -- Phase-11 memory graph
  photo_id_a INTEGER NOT NULL REFERENCES photos(id),
  photo_id_b INTEGER NOT NULL REFERENCES photos(id),
  relation   TEXT NOT NULL,              -- similar|same-person|same-place|same-event|same-tag|near-time
  weight     REAL NOT NULL,
  PRIMARY KEY (photo_id_a, photo_id_b, relation)
);

CREATE TABLE embeddings (                -- optional phase; CLIP vectors
  photo_id INTEGER NOT NULL REFERENCES photos(id),
  model    TEXT NOT NULL,
  vector   BLOB NOT NULL,
  PRIMARY KEY (photo_id, model)
);

CREATE TABLE errors (                    -- the retry queue; nothing fails silently
  id INTEGER PRIMARY KEY,
  stage TEXT NOT NULL,                   -- ingest|screen|tag|notes
  source_path TEXT,
  photo_id INTEGER,
  error TEXT NOT NULL,
  retry_count INTEGER NOT NULL DEFAULT 0,
  last_attempt TEXT NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE runs (                      -- audit trail per pipeline invocation
  id INTEGER PRIMARY KEY,
  stage TEXT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT,
  stats_json TEXT
);

CREATE TABLE stats (key TEXT PRIMARY KEY, value TEXT);   -- incl. vaulted_total
CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT);
```

Design points:

- **Identity is `sha256`, never the filename stem.** This retires the current `.tags/<stem>.json` collision bug where two different `IMG_0001.jpg` silently share one tag file.
- **`files` vs `photos`** is what makes dedup honest: five copies of one photo are five `files` rows pointing at one `photos` row; the DB remembers where every copy came from even after quarantine.
- **`errors` is the fix for silent drops.** Every skip becomes a row; `pipeline retry` re-runs unresolved rows. A sweep is not "done" until `errors` is empty or explicitly waived.
- **`model_version` on tags** enables re-tagging migrations when the model or schema is upgraded (mixed-version state is visible, never silent).

---

## 5. Pipeline stages (correct order)

The current pipeline runs screen → tag → dedup, which pays two model calls per duplicate before discarding it. The specified order is:

```
discover → ingest → dedup → screen → tag → derive (notes, thumbs, edges)
```

Each stage is idempotent and incremental (keyed on `sha256` / `(source_id, source_path)`), safe to re-run at any time.

### 5.1 Discover (exists: `photo_discovery.py`)

Walks a registered source, writes/updates `sources` + candidate file list. Read-only, parallel, includes videos and RAW in the inventory even when they're out of tagging scope. Replaces the loose JSON manifests with rows in `files` (a `--manifest-json` flag keeps the old output for eyeballing).

### 5.2 Ingest

For each new `(source, path)`: copy to `staging/`, compute SHA256 + pHash, extract EXIF (taken_at, camera, GPS, orientation) with Pillow/exiftool, insert `photos` + `files` rows, then move from staging into `originals/<year>/<month>/<sha8>-<name>`. Source file is untouched. Unreadable/corrupt files → `errors`.

Reverse geocoding of GPS → `places` uses offline `reverse_geocoder` (no network).

### 5.3 Dedup (exists: `dedup.py`, keep the union-find core)

- Exact: same `sha256` → new `files` row on the existing photo, `disposition='duplicate'`. Zero cost, no group needed for byte-identical copies from different sources — the DB *is* the record.
- Near: Hamming(pHash) ≤ threshold (default 10) via union-find → `duplicate_groups(kind='near')`, keeper chosen by resolution+size score. Losers' library copies move to `duplicates/`, `decision='pending'`.
- **Nothing is deleted without a `decision='discard-approved'` set by a human** (via review CLI now, web UI later).
- Scale fix: pairwise comparison runs per-BK-tree/bucket (prefix bucketing on pHash) instead of O(n²) over all distinct hashes.

### 5.4 Screen (two-pass; replaces single-call `screen_content.py`)

Runs on every photo that survived dedup, **before any tagging**.

```
pass 1: local NSFW classifier (Falconsai/nsfw_image_detection, ONNX/CPU ok)
        score s in [0,1]
   s < 0.20              → SAFE  (proceed to tagging)
   s ≥ 0.20              → pass 2
pass 2: qwen2.5vl yes/no confirmation (no caption generated or stored)
   VLM says explicit                    → VAULT
   VLM says safe  and  s < 0.85         → SAFE
   VLM says safe  and  s ≥ 0.85         → REVIEW (classifiers disagree hard)
any error (model down, bad image, timeout)
                                        → ERROR (errors table, retried; never a verdict)
```

- Thresholds live in config and get **calibrated on a labeled sample before the real sweep** (Track A validation): target ≥ 0.99 recall on explicit at pass 1's 0.20 threshold; tune with held-out family photos for the false-positive rate.
- **VAULT routing:** file is *moved* into the mounted LUKS container, its `photos`/`files` rows are **deleted**, and `stats.vaulted_total` increments. Thumbnails/tags were never generated (screening precedes tagging), so there is nothing else to scrub — this ordering is what makes invariant #3 cheap to guarantee.
- **REVIEW routing:** file moves to `vault:/review/`; DB rows deleted the same way. Human review (the founder or the other parent, vault mounted) either releases the file back into `staging/` (it re-enters the pipeline as safe) or moves it to the vault proper.
- Batch mode: pass 1 runs as one process over the whole batch (model loaded once), not one subprocess per photo.

### 5.5 Tag (exists: `scan_photo.py`, adapted)

Only SAFE photos reach this stage. One VLM call per photo against `tag-schema.json` (15 dimensions — People, Occasion, Emotion, Attire, Location, Activity, Time of Day, Milestone, Season/Holiday, Group Size, Quality, Pets, Humor, Sentiment, Year), JSON-format response, results written to `tags` with `model_version`. Fixes folded in:

- Model name comes from `vault_config` (currently hardcoded).
- Schema path comes from config, not `__file__`-relative guessing.
- Malformed model JSON → `errors`, not a poisoned tag file.
- EXIF `taken_at` (when present) overrides the model's year guess.
- Optional `--person/--reference` face check retained for targeted searches (e.g. the Alex-graduation hunt) until real face clustering lands (§9).

### 5.6 Derive

All human-facing artifacts, regenerated from the DB:

- **Obsidian notes** (exists: `generate_notes.py`, rewritten to read the DB): one note per photo under `Photos/<year>/`, index notes per dimension value (`People/Alex.md`, …). Notes carry a `generated: true` frontmatter flag; a `<!-- manual -->` section is preserved across regeneration so humans can annotate. `vault-notes rebuild` deletes and regenerates the whole tree deterministically.
- **Thumbnails** into `thumbnails/` (max 512px, stripped EXIF).
- **Memory-graph edges** (§9) once embeddings exist.

---

## 6. The vault (LUKS)

- `vault.img`: LUKS2 container file on the home server (`cryptsetup luksFormat --type luks2`), ext4 inside, initial size 50 GB (resizable).
- **Mount discipline:** never mounted at boot; opened only by explicit `vault-open` (prompts for passphrase, mounts at `/mnt/vault`, auto-unmounts after 30 min idle via systemd timer). The pipeline's screening stage requires the vault mounted and **halts before pass 1 if it isn't** — flagged items must have somewhere safe to go, and queueing them in plaintext is not an option.
- **Passphrase:** known only to the two parents; written backup stored offline (paper, not in any synced vault, not in `Home/API Keys.md`). No keyfile on disk.
- **No plaintext index anywhere.** If browsing/search over vault contents is ever wanted, that index lives as files *inside* the container.
- **Replication-safe:** the container replicates as an opaque blob (§10); redundancy never weakens privacy.
- **Migration of the existing `Quarantine/` folder** is step one of deployment: mount vault, move contents in, shred the plaintext originals (`shred -u`), verify the folder is empty and remove it.

## 7. Configuration

`vault_config.py` grows into the single config surface (env-overridable, as today):

```
MEMORYVAULT_ROOT          /srv/data/owner/memory-vault     (Obsidian vault)
LIBRARY_ROOT              /srv/data/photo-library
DB_PATH                   $LIBRARY_ROOT/photos.db
VAULT_IMG                 $LIBRARY_ROOT/vault.img
VAULT_MOUNT               /mnt/vault
OLLAMA_URL                http://<lan-ip>:11434/api/generate
VISION_MODEL              qwen2.5vl:3b
NSFW_MODEL_PATH           (local ONNX weights, downloaded once)
SCREEN_T_LOW / T_HIGH     0.20 / 0.85
NEAR_DUP_THRESHOLD        10
```

No script hardcodes a path, URL, or model name.

## 8. Code layout & migration from current scripts

The seven flat scripts become a small package with a single CLI; existing entry points keep working during migration.

```
MemoryVault/.scripts/
  memoryvault/
    config.py        ← vault_config.py
    db.py            (schema, migrations, connection)
    discover.py      ← photo_discovery.py
    ingest.py        (new)
    dedup.py         ← dedup.py (union-find kept, bucketing added)
    screen.py        ← screen_content.py (two-pass)
    tag.py           ← scan_photo.py
    notes.py         ← generate_notes.py (DB-driven)
    vault.py         (LUKS open/close/route helpers)
    pipeline.py      ← pipeline.py (new stage order, batch screening, retry)
  mvault             (CLI: mvault init|discover|ingest|dedup|screen|tag|notes|constellation|retry|status|review)
```

(The CLI is `mvault`, not `mv` as earlier drafted — `mv` collides with coreutils.)

`mvault status` prints the funnel: files discovered → ingested → deduped → screened (safe/vaulted/review counts as aggregates only) → tagged → noted, plus open `errors` — this becomes the heartbeat number the check-in agents report instead of guessing.

## 9. Later phases (designed now, built after v1)

- **v1.1 — Video:** ingest samples keyframes (ffmpeg scene-change + fixed interval) into a `keyframes` table child of `photos(media_kind='video')`; *every* keyframe goes through §5.4 screening, and one flagged keyframe vaults the whole video. Tagging aggregates keyframe tags.
- **v1.2 — Embeddings + search:** CLIP (`open_clip`, local weights) vectors into `embeddings`, ANN via sqlite-vec; free-text search CLI, then the small LAN web UI (timeline, facets, dupe-review queue) from PLAN.md Phase 9.
- **v1.3 — Memory graph:** `photo_edges` computed incrementally at ingest (ANN top-K for `similar`, joins for person/place/time/tag), rendered by the Brain view (§9.1). Vault content has no edges, ever.

### 9.1 The Brain — memory-graph app (since renamed Constellation, 2026-07-30)

> **Status 2026-07-23:** v0 shipped and running on the photo server VM against the
> 163-photo pilot. What shipped diverged from (and improved on) the sketch
> below: the **home view is category neurons** (tag values sized by photo
> count, co-occurrence dendrites, deterministic sunflower-spiral layout —
> physics only in the photo pathway view), tapping a photo opens a
> **full-screen lightbox** (cached ≤1600px renditions, `/display/`), and a
> **Memories mode** (`/memories`) plays a slideshow that walks the memory
> graph — each next photo connected to the current one, captioned with why.
> the founder's call: The Brain grows into a **standalone app** for tablets, TVs,
> and phones — see `CONSTELLATION-APP-SPEC.md` (formerly BRAIN-APP-SPEC.md), which supersedes this section for
> product direction; this section remains the v0 dashboard record.

The visual identity of the whole system: photos are neurons, relationships are neural pathways. Decided 2026-07-23.

**Stack.** A view of the LAN web app: static frontend (cytoscape.js or sigma.js, vendored locally — no CDN, consistent with the no-egress rule) talking to a small local API (`GET /api/photo/<id>/neighborhood?hops=1&k=8`) that reads `photo_edges` + `thumbnails/`. Runs on the home server in v1, replicates with the web app in Phase B.

**Visual language.**
- Dark field; neurons are circular photo thumbnails with a soft glow.
- Pathways are colored by relation: **gold** = same person, **blue** = same place, **violet** = visually similar, **green** = same event/day, **dim gray** = near-in-time. Edge thickness = weight. A legend keeps the graph from being a black box.
- Organic force-directed layout — brain-*like*, not constrained to a literal brain silhouette (rejected as fighting the data at scale).

**Interactive explore mode.** Tap a neuron → it centers, its pathways light up, and its 1-hop neighborhood pulls in (top-K = 8 edges per relation, from the pruned graph). Tap a neighbor → the view re-centers and the graph glides node-to-node. A side panel shows the focused photo full-size with its tags and "why related" labels. Search/date jump seeds the starting neuron.

**Ambient brain-wall mode.** Read-only, no-login (LAN-only), full-screen: the graph drifts slowly; every few seconds a neuron "fires" — brightens, its pathways pulse outward, connected photos glow and briefly enlarge — then the focus wanders along an edge to a neighbor. Firing order is weighted toward "on this day" anniversaries and recently ingested photos. Suitable for a wall tablet or spare monitor.

**Performance rule.** Never render the whole library as one physics simulation. Explore mode loads only the focused neighborhood (1–2 hops, ≤ ~200 nodes); ambient mode walks the graph the same way. This is the UI-side twin of the spec's precompute-don't-compute-on-click rule — every frame is an indexed DB read.

**Privacy.** Only SAFE photos exist in `photos.db`, so the Brain can never display vault content by construction. The no-auth ambient mode is display-only and carries no search or export surface.
- **v1.4 — Face clustering:** InsightFace/ArcFace → person clusters with human-confirmed labels; replaces the reference-photo hack.

## 10. Phase B — 2-VM infrastructure (hardware confirmed available 2026-07-23)

The Proxmox hosts exist with capacity; a dedicated VM gets created for this job:

- **Topology:** one VM per host, each holding `photo-library/` + the Obsidian vault + the web UI. **GPU inference stays on the home server** (no GPU passthrough assumed) — Ollama and the NSFW classifier are served over the LAN; the pipeline runs wherever the storage is and calls inference remotely.
- **Replication:** ZFS send/receive (preferred) or Syncthing for `originals/`, `thumbnails/`, `duplicates/`, `vault.img` (opaque). **`photos.db` is never file-synced live** — a WAL-mode SQLite file under Syncthing corrupts. Instead: Litestream continuous replication, or a cron'd `sqlite3 .backup` snapshot that replicates as a plain file. One VM is the designated writer; the other is a warm standby serving read-only UI.
- **Failure model:** either VM down → full current copy on the other; the home server down → ingest/tagging pauses (queued in `errors`/status), browsing unaffected.
- Not exposed beyond LAN/Tailscale; UI behind its own auth.

## 11. Privacy hardening checklist (release gate for the real sweep)

- [ ] No outbound calls except Ollama/classifier on LAN addresses (verify with an egress capture during a test run)
- [ ] Screening precedes tagging in code, enforced by pipeline ordering + a test
- [ ] Error ≠ verdict: dead-endpoint test shows SAFE-less skip + `errors` row, zero quarantines
- [ ] Vault mounts on demand only; pipeline halts if vault unavailable at screening time
- [ ] `photos.db` contains zero references to vaulted/review items (test: vault a fixture, grep the DB)
- [ ] Old plaintext `Quarantine/` migrated into vault and shredded
- [ ] Sources are opened read-only; no rename/move/delete under any source root (test on a read-only mount)
- [ ] Obsidian vault (git + Syncthing-synced) receives notes only for SAFE photos
- [ ] Duplicate deletion requires recorded human approval

## 12. Validation & acceptance (Track A, before any real sweep)

1. **Screening calibration:** labeled sample (safe family photos + safe-stand-in flagged set); measure pass-1 recall/precision at thresholds; document the numbers in the repo.
2. **Dedup fixtures:** existing synthetic resized/recompressed fixtures must cluster; add rotated + slightly-cropped cases and a no-false-merge check between distinct scenes.
3. **End-to-end dry run** on a ~500-photo sample folder: funnel numbers add up (`discovered = safe + vaulted_total + review + errors + duplicates`), notes regenerate byte-identically, re-running the whole pipeline is a no-op.
4. **Kill-switch tests:** kill Ollama mid-sweep (photos land in `errors`, nothing quarantined); yank the vault mount (pipeline halts pre-screening).

## 13. Rollout milestones

| # | Milestone | Contents | Gate |
|---|---|---|---|
| M1 | Package + DB | `memoryvault/` package, schema, discover/ingest/dedup rewired to DB, `mv status` | fixtures pass |
| M1.5 | Brain prototype (parallel with M1) | ~300–500-photo sample tagged + CLIP-embedded, real `photo_edges`, Brain view (§9.1) explore + ambient modes against it | renders sample graph; neighborhood loads < 1s |
| M2 | Screening + vault | LUKS container, two-pass screen, review flow, `Quarantine/` migration | §11 checklist, §12.1/12.4 |
| M3 | Tagging + notes | DB-driven tag + note generation, retry queue | §12.3 dry run |
| M4 | **Real sweep (Track B software-side)** | external drive, Windows drive, SD, phone exports — on the home server | M1–M3 gates green |
| M5 | Video (v1.1) | keyframes, video screening/tagging | keyframe screening test |
| M6 | Search & graph (v1.2–1.3) | embeddings, web UI, memory graph | — |
| M7 | 2-VM infra (Phase B) | replication, standby | hardware exists |

## 14. Remaining open questions (tracked, not blocking)

- ~~**Cloud backup target** — issue #2~~ **Resolved 2026-07-23:** the second Proxmox VM's replica is the backup; no cloud vendor. Known limitation: both copies live in the house, so fire/theft takes both — if that risk ever becomes unacceptable, add a client-side-encrypted (restic/borg) off-site copy without changing anything else.
- **Physical prints** — scanning workflow, out of scope until the digital sweep is done.
- **the other parent's separate collections** — enumerate as sources before M4 so the sweep list is complete.
- **Immich hybrid** — explicitly rejected for now (privacy-first custom build); revisit only if mobile auto-backup becomes a requirement Syncthing can't meet.

---

## 15. Shipped since v1.0 (2026-07-24 → 2026-07-28)

Recorded here so the spec matches the running system. Product naming: the
system the founder shows people is **Constellation**; **Memory Vault** is the engine
underneath it (pipeline, DB, vault). The public repo is `constellation`.

### 15.1 Video — v1.1 delivered, with a different keyframe model

§9's v1.1 sketch (a `keyframes` child table) was **not** what shipped. Videos
are first-class rows in `photos` with `media_kind='video'` and a `duration`
column; a poster frame extracted with ffmpeg is the display rendition, so every
surface that draws a photo draws a video for free.

- **Screening** samples frames across the clip and takes the *worst* verdict —
  one bad frame vaults the whole video, preserving §5.4's intent.
- **Captions** send N frames to qwen2.5vl in a single call, so a description
  covers the clip rather than one arbitrary instant.
- **Playback** is a range-capable `/video/` endpoint (HTTP 206), so TVs and
  phones can seek.
- Videos play in-frame on the gallery wall.

### 15.2 Live Photos are not videos

the other parent's library produced 4,302 "videos", of which **4,263 were iPhone Live
Photo motion clips** — the 1–3s files a phone writes beside every still. They
are detected from Apple metadata (`com.apple.quicktime.live-photo` /
`content.identifier`), skipped at ingest, and existing ones hidden as curation
`Removed` (reason `live-photo`, restorable — nothing is deleted). Without this
the library reads as mostly junk video.

### 15.3 Tagging and screening quality

- **Schema v2 tagging**: enum-constrained JSON structured output. Dimensions
  that vision cannot honestly judge (people, year) are marked `vision:false`.
- **People come only from face recognition** (InsightFace), never from the
  vision model guessing. the dog is a person, not a pet — the class of error this
  rule exists to prevent.
- **Two-pass NSFW**: a Falconsai classifier screens, qwen2.5vl confirms.
  `--rescreen` re-runs the pass over already-screened items as the filter
  improves.
- **Captions / OCR / orientation** via `mvault describe`.
- **Rescue sweep** (`curate --rescue`) pulls good photos back out of Trash;
  deletion always requires a human typing DELETE.

### 15.4 Curation and the vault

Curation bins are Trash / Removed / Delete / Kept. From a photo, a human can
open it full-size, **Move to Personal Vault** (subfolders for a partner and
`other`), or mark for permanent delete. The vault passphrase is never stored;
`MEMORYVAULT_VAULT_MODE=dir` provides a directory-mode alternative for Windows
and container installs where LUKS isn't available.

### 15.5 Sharing

`/api/photo/share` emails a photo over SMTP using the household's own sender
credentials, read from an env file outside the repo. Live as of 2026-07-28.

### 15.6 Packaging (for households that aren't ours)

- **Windows installer**: an Electron GUI wizard (welcome → scan → storage →
  download → done). `installer/scan.js` detects GPU/VRAM/storage/network across
  NVIDIA, AMD (ROCm), Intel Arc (Vulkan, experimental), integrated, and none,
  then recommends a model. It never recommends qwen2.5vl:3b — that model is
  broken for this workload.
- **Native backend**: the `mvault` CLI is bundled with PyInstaller (onedir), so
  an install needs no Docker and no Python.

### 15.7 Device requirements (measured, 2026-07-28)

The ambient display is memory-bound, not GPU-bound. Measured on an **onn 4K
Plus** (Android 14, **2 GB RAM**) via `dumpsys gfxinfo`:

| Configuration | Frame time |
|---|---|
| lite mode, photos off (fresh boot) | 31 ms (~32 fps, 1% jank) |
| full mode | 400 ms (~2.5 fps) |
| lite mode, after memory filled | 400 ms (~2.5 fps) |

At the slow readings the CPU sat **371% idle at full clock** with **786 MB of
883 MB swap in use** — the box was thrashing swap, not computing. Canvas-level
optimisations (round photo sprites replacing a per-node arc-clip-and-downscale)
did not move the number, which is the evidence that the bottleneck is resident
memory.

**Therefore: 2 GB Android TV boxes are not a supported target for the ambient
display.** Budget ~3 GB+ (NVIDIA Shield class). A low-memory mode — fewer
nodes, `?photos=N` cap, smaller thumbs, releasing decoded images — is tracked
but unbuilt.

---

## 16. Curation, reliability and deployment (2026-07-29 → 2026-07-30)

Everything here came out of running the pipeline against the real library at
scale for the first time. Most of it is not new capability — it is the
difference between a stage that works and a stage that works unattended.

### 16.1 Curation learned to find things it was missing

- **`curate --screen-captures`** asks whether an image is *a picture of a
  screen* — status bar, battery and signal icons, nav and tab bars, address
  bars, keyboards, chat bubbles, scrollbars — rather than judging its content,
  and says explicitly to answer yes even when the screen shows a photograph of
  people. That last clause matters: the older document prompt carried the
  escape hatch "if any person, face, pet or family moment appears anywhere,
  answer no", so a screenshot of a text conversation showing a profile photo
  was answered *no*. It found 78 documents in 10,671 photos (0.7%); the new
  pass runs at roughly **22%** on the same library. Candidates are restricted
  to photos with no camera EXIF — a real photograph nearly always carries one —
  which drops ~16,000 obvious photographs from the work at no cost to recall.
  Verdicts stamp `screencap_check` so a re-run does not pay twice.

- **`curate --exclude-path TEXT`** hides everything whose source path contains
  TEXT. A drive sweep picks up things that were never memories — a thirteen-part
  Six Sigma course, recorded meetings, 3D-printer test renders — and no vision
  model should judge those a frame at a time when the folder name already says
  what they are. Already-ingested items are hidden; items still staged are
  marked so ingest skips them. Naming a path is a human decision, so it
  overrides an automated 'Kept'. **Preview the match before running it**: the
  obvious pattern "MovieMaker" would have binned an edit of the kids' Lego
  videos.

- **`rescue()` will not resurrect anything below `MIN_PX`.** It had no size
  guard, so it asked the model about a 16x16 favicon upscaled to 640px, got a
  reasonable "yes, that looks like a photo", and put **1,299 icons back on the
  wall as Kept** — 1,883 sub-200px images were visible in total. Size is not a
  judgement call, so it is no longer the model's to make.

### 16.2 A stage can no longer hang the queue

A `tag --retag` shard sat wedged for **22 hours** against an
ESTABLISHED-but-dead socket — a day of elapsed time against 19 minutes of CPU —
holding up the nightly backlog, a video ingest, and a document sweep that
consequently never ran. Its `requests.post(timeout=120)` never fired.

- **`vision_http.py`** is now the only place the pipeline talks to the vision
  server, with three independent defences, because the reason that timeout
  didn't fire was never established and a guess is not a fix: TCP keepalive so
  a peer that vanishes without FIN/RST becomes a socket error; separate connect
  and read timeouts, since `requests`' scalar timeout bounds a socket operation
  and not the call; and a hard wall-clock ceiling via SIGALRM, capped absolutely
  at 900s — deriving it only as a multiple of the caller's timeout meant a
  careless `timeout=9999` bought a seven-hour ceiling, the very failure being
  fixed.

- **`deploy/mvault-watchdog.sh`** covers hangs that are not vision calls. It
  detects by **CPU time, not log output**, because a wedged process burns no CPU
  whatever wedged it. The threshold sits above the vision ceiling so a stage
  legitimately blocked on inference is never killed. It has since caught real
  stalls in production within 25 minutes.

- **`screen()` retries on a locked database.** `rescreen()` next door already
  did; `screen()` wrote bare, so a concurrent stage killed it — and because the
  calling script logged `screen error` and carried on to print DONE, **241
  unscreened items reached the display**. Screening is the one stage that must
  never fail quietly: failures now surface, and one unreadable photo no longer
  strands the rest.

- **The second GPU box is the common factor in every hang so far** (four,
  including the original). It answers health checks and then wedges
  mid-generation. Until that is understood, long sweeps run single-process on
  the reliable card: half the throughput beats half the work silently dying.

### 16.3 Memory: decode at the scale you actually need

`model_image_b64` wanted a 1024px JPEG but fully decoded the source first.
On the 108MP phone shots in this library that materialises ~324MB of pixels
purely to be discarded by the next line — **1262MB peak RSS for one image**.
With two shards running that is a 2.5GB spike on a 12GB box, which is very
likely what drove it into swap hard enough to need a hypervisor reset.
`im.draft()` decodes at reduced scale: **0.42s and 39MB against 1.56s and
1262MB**, output essentially identical. A stage swapping on a huge decode also
presents exactly like a hang — no CPU, no progress, no error — which is why the
network defences correctly did not fire on it.

### 16.4 The vault review queue is actionable, and handles video

`release_from_review` / `keep_in_vault` / `delete_from_review` existed but were
reachable from nothing — no CLI, no API — so the screener filled `vault/review`
with no way for a human to empty it. Now `mvault vault review
[--release|--keep|--delete FILE...]`, wrapped by `deploy/constellation` for
day-to-day use.

Releasing a **video** used to raise: the code opened every file with PIL, which
cannot open `.mov`, and hardcoded `media_kind='photo'`. Since verdicts are
issued in batches, one video aborted every remaining file. Videos now take a
path mirroring ingest — ffprobe, poster frame, thumbnail from the poster,
duration recorded — and a batch survives a bad file.

**Reviewing video needs to show video.** The contact sheets render with PIL, so
every `.MOV` in the queue appeared as the word "unreadable"; 27 unscreened
videos were consequently waved through a review that could not see them. Sheets
now render a **filmstrip sampled across the clip** — one poster frame cannot
tell you what is in the middle of a video — and an unrenderable cell says so in
red instead of failing quietly.

### 16.5 Deployment: one path, from git only

Two sessions were copying files to the pipeline host independently and
clobbering each other. One renamed the package `brain/` → `constellation/`; the
other's `cli.py` landed on top and removed a flag added minutes earlier. The
quieter version came first: against a stale `cli.py`, argparse **prefix-matched
`--screens` to `--screenshots`** and ran the wrong pass without erroring — a
sweep reported success having checked five photos. The flag is now
`--screen-captures`, which cannot be a prefix of the older one, so a stale
deploy fails loudly.

The host cannot `git pull` — its remote pointed at the old org and it has no
deploy key. **`deploy/deploy-vm.sh`** therefore exports a tree from a pushed ref
and rsyncs it, so deployed state is always a committed, pushed commit and never
one session's working copy. It refuses a dirty tree, uses `--delete` so renamed
packages actually disappear rather than lingering as importable stale modules,
imports the package on the host *before* restarting so a broken deploy leaves
the old tree serving, finds the systemd unit rather than hardcoding a name that
has already changed once, and checks for HTTP 200 — because "systemd says
active" is not the same as "it serves".

### 16.6 Edges: bounded by construction, not by hope (2026-08-02)

`mvault edges` was OOM-killed on both 2026-08-01 and 2026-08-02 — 10.2GB RSS on
an 11GB VM with no swap — so the nightly pipeline ended on a dead stage and the
graph stayed frozen at **148 edges**. Two separate quadratics, one root cause:
generate everything, then keep top-K.

- **Group relations** built `combinations(pids, 2)` per shared tag. "the founder"
  alone covers 2,546 photos = 3.2M pairs; the four family tags plus per-day
  buckets are ~12M dict entries. Every pair in a group carries the *same*
  weight, so the top-K prune was breaking a 2,546-way tie arbitrarily. Groups
  are now walked in time order and each photo links to its `GROUP_FANOUT = 12`
  nearest-in-time group-mates: bounded at `fanout × n`, and the survivors are
  the ones a person would actually browse to — what came next.
- **`similar`** reused `dedup._candidate_pairs`, whose pigeonhole bucketing
  holds every candidate pair in a `seen` set. At 21,458 hashes ~64% of all
  pairs share a nibble — ~150M tuples. That set was the bulk of the 10.2GB.
  pHashes are 8 bytes, so the library now goes into a packed numpy array and
  distances come from a chunked XOR + popcount sweep: exact at every distance
  (the bucketing was only exact for ≤15, while this relation asks for ≤22),
  bounded to `CHUNK` rows of intermediates, and it returns top-K per photo
  instead of everything in the band. `dedup` uses the same sweep.

Measured against a snapshot of the live 22,388-photo library: **24.5s and 364MB
peak RSS, 250,891 edges** (near-time 9,238 / same-event 69,323 / same-person
58,739 / same-place 4,702 / similar 108,889). `near-time` had been near-dead —
it skipped any month bucket over 30 photos, i.e. every real month — and now
chains one representative per day across the month, which is the relation it was
always meant to be. Runs report `fanout` in their stats so a thin graph reads as
"capped here", not "broken".

### 16.7 A file that can never be read stops costing a run every night

Ingest failures left the `files` row at `disposition='discovered'`, so every
nightly run re-read the same broken bytes, and `record_error` inserted a fresh
row per attempt. By 2026-08-02 that was **431 files re-failing nightly and
14,970 error rows** — identical counts on Jul 31, Aug 1 and Aug 2 — with real
errors buried in the repeats. The `retry_count` column had existed, unused,
since the first schema.

`record_error` now keeps **one open row per (stage, target)**, bumps
`retry_count`, and returns the attempt count. After `MAX_ATTEMPTS = 3` ingest
dead-letters the file to `disposition='failed'` and says so on stdout; the count
surfaces as `files_failed` in `mvault status`. Nothing is lost: `mvault retry`
releases every dead-lettered file and clears the counts, because asking for a
retry should mean retry. A one-time migration folds legacy repeat rows into one
row per target carrying the count forward — a file that already failed 19 nights
running should not get three more. On the live snapshot: 15,092 error rows →
6,881, the 431 retired on the first run, ingest queue drains to zero.
