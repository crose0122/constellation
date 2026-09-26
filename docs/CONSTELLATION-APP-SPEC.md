# Constellation — Family Memories App Specification

> Renamed from "The Brain" 2026-07-30 (file was BRAIN-APP-SPEC.md); the
> server, service and assets follow: `mvault constellation`,
> `memoryvault-constellation.service`, `constellation.js`/`.css`.

**Date:** 2026-07-23
**Status:** Approved direction (the founder: "it should probably be an app, not just a dashboard")
**Relationship to SPEC.md:** SPEC.md §9.1 remains the record of the v0 web dashboard;
this document owns product direction for Constellation from here.

---

## 1. What it is

The Brain is the family's window into the Memory Vault: photos as living
neurons, relationships as glowing pathways, and a Memories stream that plays
the library as a connected story rather than a shuffled slideshow. It is a
**product surface in its own right** — something you install on a wall
tablet, open on the TV, or carry on your phone — not an admin dashboard for
the pipeline.

Non-negotiables inherited from SPEC.md: LAN-only, no cloud, no telemetry;
vault content can never appear (it isn't in `photos.db` by construction).

## 2. The three experiences

| Mode | Where it lives | What it does |
|---|---|---|
| **Explore** | phone / tablet, in hand | Category neurons (sunflower-spiral, count-sized) → tap → photo grid → full-screen photo → its pathway graph. The "find that photo / wander the archive" tool. |
| **Ambient** | wall tablet, spare monitor | The neuron web idles and fires on its own; pure atmosphere, no login, display-only. |
| **Memories** | tablet on a shelf, the TV | Full-screen slideshow that **walks the memory graph**: each next photo is connected to the current one and the caption says why ("same day", "same people"); seeds weight on-this-day anniversaries; Ken Burns + crossfade; tap = next. |

All three exist today (v0, web). The app work below is about how they're
delivered, not what they are.

## 3. Why "app" and what that takes (decided path)

A browser tab has three problems on a shelf device: the URL bar, the screen
timeout, and no home-screen identity. The path, in order of effort:

### Phase A — installable PWA (next)
- `manifest.json` (name "Memories", icons, `display: fullscreen`,
  `start_url: /memories`), service worker with a minimal offline shell.
- **Requires HTTPS.** Browsers only grant real install (Android WebAPK,
  no chrome, survives reboots) on secure origins. Plan: reuse the the household system
  family-CA + Caddy pattern (turnkey-HTTPS work;
  the home server mkcert precedent) → `https://memories.local` (or
  `the photo server VM` cert) issued by the family CA, CA installed once per device.
- Wake-lock API to keep the tablet screen on in Memories mode.

### Phase B — dedicated shelf/TV deployment
- Tablet: install the PWA, or Fully Kiosk Browser pointed at `/memories`
  (kiosk mode, scheduled screen on/off, motion wake).
- TV: Android TV browser/kiosk app at `/memories`; caption type already
  scales to 10-foot viewing. Chromecast receiver is a stretch goal.

### Phase C — packaged app (only if PWA falls short)
- Capacitor wrapper (same web code) → sideloaded APK for Android tablets /
  Android TV. Native shell buys: boot-to-app, quieter updates, TV remote
  d-pad input. No cloud services appear in any variant.

## 4. App-grade requirements (gaps v0 doesn't meet yet)

1. **Library quality gate** — the Memories stream surfaced an ingested app
   screenshot as a "memory." Before the app is family-facing: minimum-size
   filter at ingest, and route `Screenshot/document`-tagged items out of
   the photo stream (schema already captures this).
2. **Full library** — 10,975 photos ingested; screening+tagging of the
   remaining ~10.8k runs after the vault ceremony. The app is only as good
   as its graph.
3. **Resilience** — pipeline runs and VM reboots must never leave a wall
   tablet on an error screen: the boot-retry loop exists; add SW offline
   shell + auto-reconnect splash in Phase A.
4. **Multiple viewers** — Brain server is threaded and read-only over the
   DB; several devices can watch different Memories streams concurrently.
   Keep it that way (no per-client server state).
5. **Screen scaling** — done for phone/tablet/TV type; verify 4K TV.

## 5. Open questions (tracked, not blocking Phase A)

- **Names**: "The Brain" (explore) vs the shelf product ("Memories"?) —
  what does the family call the tablet on the counter?
- **Voice**: "show me Alex's graduation" via the household system voice stack — later,
  after face clustering (SPEC.md v1.4) makes people queries reliable.
- **the household system integration**: does The Brain ship as a the household system hub surface
  (kiosk page / ACE lever) for beta families, or stay a Photo-Project-only
  product? Decide after the household has lived with it.
- **Curation controls**: per-category include/exclude for the Memories
  stream (e.g., never show `Quality: Blurry`), favorites boost.

---

## 6. Shipped surfaces (as of 2026-07-28)

The app is called **Constellation**. Surfaces now live on its server (formerly "the Brain server"):

| Route | What it is |
|---|---|
| `/` | the constellation sphere — category neurons wearing photos. The **Index** (topbar button, 2026-07-30) opens a glass sheet listing *every* category grouped by dimension in its hue, star-dots sized by count — the sphere shows the top ~40, the Index shows all; any entry clicks straight into its `/node` galaxy. Data from `/api/index` (all dim/value counts, no edge math, 10-min cache) |
| `/wall` | gallery wall: framed art in ornate gilt frames, videos playing in-frame |
| `/gallery` | grid browse, lightbox with tags |
| `/memories` | graph-walking slideshow |
| `/menu` | launcher linking every surface (View / Manage) |
| `/progress` | pipeline dashboard, including a video section |
| `/people`, `/curation` | face management and the triage queue |

**Kiosk full loop** (the founder's spec): constellation 1 min → slideshow 5 min →
constellation 1 min → wall 5 min → repeat. Implemented across separate pages by
handing off through a `kioskNext` flag in `localStorage`.

**Adaptive lite mode** (`?lite=1`, or auto-detected from sustained slow frames):
DPR capped to 1, 30 fps cap, capped concurrent wall videos, and a `?photos=N`
cap on how many neurons wear photos.

## 7. Native Android app (replaces the Capacitor plan in §3)

Shipped as a small Kotlin app (`android/` in the constellation repo) rather than
a Capacitor wrapper — it needed to be a *screensaver*, which Capacitor doesn't
give you:

- **`MainActivity`** — kiosk mode: immersive full-screen WebView,
  `FLAG_KEEP_SCREEN_ON`, long-press opens Settings.
- **`ConstellationDream`** — a `DreamService`, so Constellation appears under
  Settings → Screensaver as a real system screensaver. This is the free
  replacement for Fully Kiosk's paywalled screensaver feature.
- **Leanback launcher entry** so it appears on Android TV home screens.
- Server address is a build property (`-PconstellationUrl=…`), empty by
  default; with none set the app opens Settings on first run. No household's
  address ships in the public repo.
- minSdk 26, targetSdk 34, cleartext HTTP allowed (the server is a plain-HTTP
  box on the LAN).

Installs by sideload (`adb install`, Downloader app, or USB).

## 8. Hardware requirement (measured, not estimated)

See SPEC.md §15.7. The display is **memory-bound**. A 2 GB Android TV box (onn
4K Plus) thrashes swap and renders at ~2.5 fps with the CPU 371% idle; it is
**not a supported target**. Budget 3 GB+ (NVIDIA Shield class) for the ambient
display, or build the tracked low-memory mode.

An alternative worth considering if cheap boxes must be supported: render the
scene server-side and stream pixels to the device, so the TV box only decodes
video. That moves the cost to the VM (which today has no GPU) and is a
meaningful build — not attempted.

## 9. The gallery wall's frames (2026-07-29)

The wall hangs each picture in a frame, and the frames are **generated, not
drawn by hand**: `tools/make_frames.py` paints them as raster pixels with
numpy/PIL and writes `constellation/static/frames.css` as CSS `border-image` rules.
Regenerate with `python3 tools/make_frames.py` after editing a timber.

Eight profiles, dealt from a weighted deck keyed on cell index (so the wall
does not reshuffle its own styles on every repaint): walnut, mahogany, oak,
cherry, ebonised, driftwood, and giltwood / silverleaf as leaf over a carved
ground. Mats come in near-black rag with a gilt fillet, warm linen, and pale
rag; roughly one frame in six takes an oval aperture.

Three things make a frame read as timber rather than a brown gradient, and all
three are in the generator:

- **grain** — fractal value noise stretched along each rail, then displaced by
  a slow wander so the fibre drifts and converges instead of ruling straight;
  plus faint cathedral figure and pore speckle.
- **profile** — the carved cross-section, as a light ramp across the rail:
  outer arris, crown, cove, inner fillet, rabbet shadow. Per-rail lighting
  puts the top rail in light and the bottom in shadow, which is what makes a
  flat ring read as a raised object.
- **mitre** — four lengths cut at 45°, so the grain *turns* at every corner
  with a seam running diagonally out of it. Grain that flows continuously
  around the perimeter looks wrong even when you can't say why.

### 9.1 Constraints worth knowing before changing the art

These were each found the hard way, by rendering and looking:

1. **SVG filters do not survive `border-image` in Android's WebView.** Two
   earlier versions drew the frames as vector gradients with an SVG `<filter>`
   supplying the grain; the filter was silently dropped at rasterisation and
   the timber arrived perfectly smooth on the TV. Raster pixels avoid the
   whole class of problem.
2. **Noise frequency must be scaled to the rail width, not the canvas.** A
   rail is only `BAND` px across, so per-canvas frequencies land as a handful
   of fat blobs and the wood reads as watered silk.
3. **~18 grain lines per rail is the ceiling.** Finer than that hits pixel
   pitch and aliases into corduroy.
4. **`border-image` must `stretch`, not `round`.** The grain does not tile, so
   every repeat shows a seam. Stretching runs along the fibre, which is simply
   what a longer length of the same moulding looks like.
5. **Iterate on a local contact sheet, not on the device.** Rendering the
   eight frames to a PNG and looking at it takes a second; a deploy-and-
   screenshot round trip to the TV takes two minutes.

Open: the palettes are a first pass and are the most likely thing to change —
they are one line per timber in `WOODS`.

### 9.2 Photographic frames replace the mouldings on the wall (2026-07-29)

The wall now hangs its pictures in **photographic antique frames**: real frame
art (`static/frame-*.png`, seven profiles — rococo gilt, baroque gilt, baroque
dark, baroque sage, quatrefoil gilt, round gilt, oval ebony) with the picture
window cut to transparency. Each profile's window is a percentage box recorded
in `FRAME_ART` in `wall.html`; those values come from the Constellation design
system (`components/media/Frame.jsx`) and must stay in sync with it. The art
**never stretches**: each frame box is sized to the art's native aspect ratio,
contain-fit and centred in its grid cell (the design kit stretched art to the
cell; on real screens that read as squashed ornament, 2026-07-30). Profiles
are still dealt from shape-matched decks (tall / near-square / wide) so the
chosen frame wastes as little wall as possible, and the drop-shadow lives on
the static art layer rather than the container — a filter above a playing
video re-rasterises every frame on a budget TV box.

The generated `f-*` mouldings and `tools/make_frames.py` (§9) remain in the
repo but the wall no longer links `frames.css` — the design-system pass of
2026-07-29 removed the generated fills from the salon hang in favour of the
photographic art. §9.1's constraints still apply if the generator is ever
rehung.

All seven PNGs derive from the design project's originals. (`frame-rococo-gilt.png`
and `frame-baroque-sage.png` briefly shipped as symmetry-mirror
reconstructions — the design MCP caps a file read at 256 KiB and truncated
the two biggest assets — until the real art arrived via a project export,
2026-07-30.)

**Art quality pass (2026-07-30).** Each frame was cleaned at native
resolution (alpha despeckle, defringe to the nearest solid colour, light
edge feather — the raw cutouts had ragged background fringe) and then
super-resolved with Real-ESRGAN x4plus to ~700–1200 px so the TV no longer
upscales blurry sources. CPU inference via a hand-rolled RRDBNet
(`torch`), because the rig's Vulkan path (llvmpipe while the NVIDIA driver
is wedged) scrambles ncnn tiles. Output is colour-matched back to each
original; windows flood-fill within 0.6% of the `FRAME_ART` openings.
Caveat: the two smallest sources (`baroque-dark`, `baroque-gilt-small`,
176 px wide) needed 4x, where ESRGAN visibly reinterprets fine scrollwork —
faithful-but-softer 2x alternates were generated and can be swapped in if
the reinterpretation bothers anyone up close.

### 9.3 Gallery placards (2026-07-30)

Every picture on the wall wears a placard, like a real gallery — but funny.
`mvault placards` is a text-only sweep in the house pattern (marker table
`placards`, resumable, `--shard`/`--limit`, wired into the nightly after
`tag`): qwen gets one photo's *verified* facts — face-recognition people,
activity/occasion/location tags, geocoded place, date, and the describe
pass's caption — and answers as a fond, slightly pretentious curator:
`«Cake Wars» — Alex tries to seize victory while Bailey watches, circa
2019`. The voice contract lives in the prompt: dry, warm, never mean, never
explains the joke, never invents names (unnamed people become 'the artist',
'a small collaborator'). `/api/gallery` returns `placard` when the sweep has
written one; the wall's plates and its lightbox show it, falling back to the
date until the sweep catches up.

**Salon hang, round 2 (2026-07-30, from the look Q&A with the founder).** The wall
is a packed salon now: ~15 frames on a TV (grid divisor 320, cols≤6, rows≤5;
LITE keeps a lighter hang), two 2x2 feature frames on walls ≥5 columns
(rococo + sage, placed half the wall apart), and a lived-in scatter — sizes
65–100% of contain-fit (floor 110px), drift to nearly touching, tilt up to
±2.5°, features full-size and level, all hashed from slot index. Sliver
cells left by feature-overflow rows hang nothing; timbers clamp to 0.6–1.5
aspect so a moulding never becomes a plank. The generated f-* timbers are
back in the decks as the quiet mat-bearing frames between the antiques
(`frames.css` re-linked). Swaps slowed to 12s. The backdrop got a faint warm
plaster veil, and the sphere iframe runs `/ambient?calm=1` — a new constellation.js (then brain.js)
mode that keeps ambient chrome but skips the auto-firing walk, because a
backdrop that re-zooms itself upstages the photographs. Every picture wears
a placard plate (`.cap`, always visible): the pipeline's witty gallery label
once §9.3 fills it in, the date until then.

**Calm is a backdrop, not a second UI (2026-07-30, same-day fix).** As
shipped, round 2's sky bled through: category labels, counts, node photo
faces, and "Our Family" all read clearly between the frames — two pages
stacked. Calm mode now also drops every label (`queueLabel` no-ops) and the
node photo faces (`assignPhoto` no-ops, which also stops the backdrop
streaming thumbnails it never shows); what remains is stars, pathways,
twinkle, and orbits. On the wall the veil strengthened (`aa/99` alphas) and
the sky iframe is dimmed (`brightness(.5) saturate(.8)`) so the brightest
star stays below the dimmest photograph, and the "← constellation" hint
fades out 12s after load. The server answers `database is locked` with a
JSON 503 ("library busy") instead of a traceback 500, and `mvault placards`
refuses to start while another sweep is running unless `--force` — the
2026-07-30 morning lock storm was a manual two-shard backfill overlapping
the nightly's own sweep.

**The sky shows through (2026-07-30).** The wall no longer sits on a plaster
gradient: a chrome-less live sphere (`/ambient`, which has no kiosk autopilot
and so never navigates itself) runs in a `pointer-events: none` iframe at
negative z behind the frames, inheriting `?lite`. The Memories overlay's veil
went translucent (`#010409aa`) for the same reason — the render loop never
pauses, so the constellation keeps glowing behind a letterboxed memory
instead of black bars. Both per the founder's call on the the family's displays.
