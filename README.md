# Constellation

**Constellation** is a self-hosted, privacy-first family photo system — your library rendered as a living 3D star map of the people, places, and moments in your life. It runs entirely on your own hardware on your own LAN: no cloud, no accounts, no telemetry, nothing ever leaves your network.

*Memory Vault* is the engine underneath — the ingest → screen → tag → faces → geocode pipeline and the local web server that Constellation is the face of.

The star map has a star for every category — people, places, occasions,
emotions — each wearing a rotating photo, with slideshows that walk the
connections between memories ("same day", "same people"). It installs as a
full-screen app (PWA) on tablets and phones.

## What it does

- **Ingest & dedup** — sweeps photo folders (SHA-256 identity, EXIF, HEIC
  support), exact + perceptual dedup
- **AI tagging** — a local vision model (qwen2.5vl via Ollama) tags every
  photo across enum-constrained dimensions: occasion, emotion, location,
  activity, season, quality, pets…
- **Face recognition** — InsightFace clustering; you name a cluster once and
  every photo of that person is tagged, searchable, and correctable
  face-by-face (with permanent "not them" bans)
- **Captions + OCR** — one-sentence descriptions and text read out of photos
  power free-text search ("kids in the pool")
- **Places** — offline reverse-geocoding from GPS EXIF (no network calls)
- **Screening & vault** — two-pass explicit-content screening routes flagged
  photos into a LUKS-encrypted vault that only humans can open
- **Curation** — heuristics + a model "rescue" pass triage junk (screenshots,
  documents, cache files) into reviewable bins; deletion always requires a
  typed confirmation
- **The Constellation** — a stdlib-only web server: constellation, gallery, people,
  memories slideshow, curation, live pipeline progress

## Install it (the setup app — recommended)

A desktop wizard that takes you from nothing to your photos on screen, with no
terminal, no Python and no Docker. Download **Constellation Setup** for your
platform and run it; it will:

1. **Scan your hardware** — finds your graphics card and its VRAM, and picks
   the vision model that will actually run well on it (you can override
   GPU vs CPU).
2. **Ask where your photos are** — pick any number of folders. They are only
   ever *read*; originals are never moved or modified.
3. **Install the AI** — fetches Ollama and pulls the vision model, with live
   download progress. This is the one big download (~6 GB).
4. **Add your photos** — reads and indexes them, so the app opens with your
   library actually in it.
5. **Hand you the app** — opens Constellation, and tells you the address to
   type into a TV or tablet.

Tagging, face recognition and place lookup keep running in the background
after the wizard closes; the app's **Progress** page shows them filling in. On
a CPU-only machine that's an overnight job for a large library — the app is
usable the whole time.

To build the setup app from source, see [`installer/README.md`](installer/README.md).

## Quick start (Docker)

The whole system in one command. You need [Docker](https://docs.docker.com/get-docker/)
with Compose.

```bash
git clone https://github.com/crose0122/constellation.git
cd constellation
cp .env.example .env                 # optional: edit paths/port
docker compose up -d                 # starts Ollama + Constellation
docker compose exec ollama ollama pull qwen2.5vl:7b   # one-time model download (~6 GB)

# point it at your photos (default: the ./photos folder) and run a pass:
docker compose run --rm memoryvault pipeline

# open the app:
#   http://localhost:8484
```

Re-run `docker compose run --rm memoryvault pipeline` whenever you add photos —
it only processes what's new. The web UI stays up the whole time.

**GPU (optional, much faster tagging):** uncomment the `deploy:` block under
`ollama` in `docker-compose.yml` (needs the NVIDIA Container Toolkit). Without
a GPU, tagging runs on CPU — correct, just slow; leave the pipeline running
overnight for a large library.

**A note on the vault:** flagged/explicit photos are routed out of the main
library into a separate "vault" volume. In Docker this is a plain Docker
volume, so **keep your Docker host on an encrypted disk**. For true at-rest
encryption, run the vault as a LUKS container on the host instead
(`MEMORYVAULT_VAULT_MODE=luks`) — see `deploy/vault-ceremony.sh`.

## Quick start (bare metal / Python)

```bash
cd scripts
python3 -m venv venv && venv/bin/pip install -r requirements-backend.txt
venv/bin/python mvault init
venv/bin/python mvault discover /path/to/your/photos
venv/bin/python mvault ingest
venv/bin/python mvault curate              # bin screenshots/junk (restorable)
venv/bin/python mvault vault create        # encrypted vault (interactive)
venv/bin/python mvault screen
venv/bin/python mvault tag                 # needs Ollama + qwen2.5vl:7b
venv/bin/python mvault faces scan && venv/bin/python mvault faces cluster
venv/bin/python mvault constellation       # http://localhost:8484
```

Configuration is entirely environment-driven — see `scripts/memoryvault/config.py`
for every knob (library root, Ollama URL, thresholds, SMTP for photo sharing).
Edit `schema/tag-schema.json` to put your own family's names in the people
dimension (they're placeholders); face recognition learns the real faces when
you label clusters on the `/people` page.

`deploy/nightly-pipeline.sh`, `deploy/systemd/`, and `deploy/vault-ceremony.sh` are working
examples from a real deployment — adjust paths/hosts to your setup.

## Put it on your TV

Constellation is at its best as an always-on display. Any device on the same
network can show it — there is nothing to sync and no account to sign in to.

- **Any TV or tablet with a browser:** open `http://<your-computer>:8484/?lite=1`.
  The `?lite=1` suffix uses a lighter render path for low-powered boxes.
- **Android TV boxes (the ONN box, Chromecast with Google TV, Fire TV) and
  Android tablets:** sideload the small native app in [`android/`](android/README.md).
  It runs full-screen as a **kiosk** photo frame, and registers as a **system
  screensaver** so the display comes up whenever the box goes idle. No paid
  kiosk app needed.

The setup wizard prints the exact address to type in on its final screen.

## Design principles

1. Source folders are read-only. Originals are never modified.
2. Deletion always requires an explicit human confirmation. Bins are
   restorable until purged.
3. Flagged content lives only in the encrypted vault; opening it takes a
   human with the passphrase, which is never stored anywhere.
4. People tags come from trained face recognition, never from a vision
   model guessing names.
5. Everything works — and stays — on your LAN.

## License

All rights reserved (for now). Open an issue if you'd like to use it and
licensing will get sorted.
