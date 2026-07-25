# Memory Vault

A self-hosted, privacy-first family photo system. Everything runs on your own
hardware on your own LAN — no cloud, no accounts, no telemetry, and nothing
ever leaves your network.

**Constellation** is the heart of it: your library rendered as a living 3D
star map of categories — people, places, occasions, emotions — each star
wearing a rotating photo, with slideshows that walk the connections between
memories ("same day", "same people"). It installs as a full-screen app (PWA)
on tablets and phones.

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
- **The Brain** — a stdlib-only web server: constellation, gallery, people,
  memories slideshow, curation, live pipeline progress

## Quick start

```bash
cd scripts
python3 -m venv venv && venv/bin/pip install pillow pillow-heif imagehash \
    insightface onnxruntime opencv-python-headless reverse_geocoder requests
venv/bin/python mvault init
venv/bin/python mvault discover /path/to/your/photos
venv/bin/python mvault ingest
venv/bin/python mvault vault create        # encrypted vault (interactive)
venv/bin/python mvault screen
venv/bin/python mvault tag                 # needs Ollama + qwen2.5vl:7b
venv/bin/python mvault faces scan && venv/bin/python mvault faces cluster
venv/bin/python mvault brain               # http://localhost:8484
```

Configuration is entirely environment-driven — see `scripts/memoryvault/config.py`
for every knob (library root, Ollama URL, thresholds, SMTP for photo sharing).
Edit `schema/tag-schema.json` to put your own family's names in the people
dimension (they're placeholders); face recognition learns the real faces when
you label clusters on the `/people` page.

`nightly-pipeline.sh`, the systemd units, and `vault-ceremony.sh` are working
examples from a real deployment — adjust paths/hosts to your setup.

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
