"""Video support helpers (SPEC v1.1).

A video becomes a first-class `photos` row with media_kind='video'. A poster
frame is extracted at ingest and saved as the display rendition, so every
image-based surface (thumbnails, constellation node images, gallery tiles,
tagging, faces, screening) works on the poster with no special-casing — while
the original video file is served for playback. Screening samples SEVERAL
frames, not just the poster, so nothing explicit slips past a tame first frame.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import config

# extensions we treat as playable video (mirrors config.VIDEO_EXTENSIONS)
PLAYABLE = {".mp4", ".mov", ".m4v", ".webm"}   # browser <video>-friendly


def probe(path: str) -> dict:
    """ffprobe → duration (s), width, height, taken_at (ISO), gps lat/lon.
    Everything is best-effort; missing fields come back None."""
    out = {"duration": None, "width": None, "height": None,
           "taken_at": None, "gps_lat": None, "gps_lon": None,
           "live_photo": False}
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=60)
        data = json.loads(r.stdout or "{}")
    except Exception:
        return out
    fmt = data.get("format", {})
    try:
        out["duration"] = round(float(fmt.get("duration")), 1)
    except (TypeError, ValueError):
        pass
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    # iPhone Live Photos ship a ~3s .mov paired with the still — Apple tags the
    # movie so we can tell it apart from a real video and not clutter the library
    out["live_photo"] = any(
        k.startswith("com.apple.quicktime.live-photo") or
        k == "com.apple.quicktime.content.identifier" for k in tags)
    ct = tags.get("creation_time")
    if ct:
        # "2023-06-01T12:00:00.000000Z" → "2023-06-01T12:00:00"
        out["taken_at"] = ct.replace("Z", "").split(".")[0][:19]
    loc = tags.get("com.apple.quicktime.location.iso6709") or tags.get("location")
    if loc:
        out["gps_lat"], out["gps_lon"] = _parse_iso6709(loc)
    for s in data.get("streams", []):
        if s.get("codec_type") == "video":
            out["width"] = s.get("width")
            out["height"] = s.get("height")
            break
    return out


def _parse_iso6709(s: str):
    """'+37.7749-122.4194+010.5/' → (37.7749, -122.4194). Best-effort."""
    import re
    m = re.findall(r"[+-]\d+(?:\.\d+)?", s)
    if len(m) >= 2:
        try:
            return float(m[0]), float(m[1])
        except ValueError:
            pass
    return None, None


def extract_frame(video: str, out_jpg: Path, at_seconds: float,
                  max_px: int = 1280) -> bool:
    """Grab a single frame at `at_seconds` into out_jpg, scaled to <= max_px
    on the long side. Returns True on success."""
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-ss", f"{max(0, at_seconds):.2f}",
             "-i", str(video), "-frames:v", "1",
             "-vf", f"scale='min({max_px},iw)':-2",
             "-q:v", "3", str(out_jpg)],
            capture_output=True, timeout=120, check=True)
        return out_jpg.exists() and out_jpg.stat().st_size > 0
    except Exception:
        return False


def poster_path(sha256: str) -> Path:
    """The poster IS the display rendition — one image, reused everywhere."""
    return config.LIBRARY_ROOT / "display" / f"{sha256[:16]}.jpg"


def is_live_photo(path: str) -> bool:
    """True if this .mov is the motion half of an iPhone Live Photo (the still
    is ingested separately, so we hide these rather than treat them as videos)."""
    return bool(probe(str(path)).get("live_photo"))


def make_poster(video: str, sha256: str, duration: float | None) -> Path | None:
    """Extract a representative frame (~25% in, avoids black lead-ins) as the
    display rendition. Returns the poster path or None."""
    at = (duration or 0) * 0.25 if duration else 1.0
    out = poster_path(sha256)
    return out if extract_frame(video, out, at) else None


def sample_frame_paths(video: str, sha256: str, duration: float | None,
                       n: int = 3) -> list[Path]:
    """N frames spread across the video, for screening (a tame poster must not
    let explicit content elsewhere in the clip through). Written to a temp
    dir; caller cleans up. Always includes the poster if it exists."""
    frames: list[Path] = []
    p = poster_path(sha256)
    if p.exists():
        frames.append(p)
    tmp = config.LIBRARY_ROOT / "staging" / f"screenframes-{sha256[:16]}"
    tmp.mkdir(parents=True, exist_ok=True)
    dur = duration or 0
    fracs = [0.1, 0.5, 0.85][:max(1, n)]
    for i, fr in enumerate(fracs):
        f = tmp / f"{i}.jpg"
        if extract_frame(video, f, dur * fr if dur else i + 0.5, max_px=640):
            frames.append(f)
    return frames


def caption_frames(video: str, sha256: str, duration: float | None,
                   n: int = 4) -> list[Path]:
    """N frames evenly spaced across the clip (higher-res than the screening
    samples) for a multi-frame caption — lets the model describe what happens
    ACROSS the video, not just one poster. Temp files; caller cleans up."""
    tmp = config.LIBRARY_ROOT / "staging" / f"capframes-{sha256[:16]}"
    tmp.mkdir(parents=True, exist_ok=True)
    dur = duration or 0
    out: list[Path] = []
    n = max(1, n)
    # evenly spaced, avoiding the very ends (black lead-in / trailing fade)
    fracs = [(i + 0.5) / n for i in range(n)] if n > 1 else [0.25]
    for i, fr in enumerate(fracs):
        f = tmp / f"{i}.jpg"
        if extract_frame(video, f, dur * fr if dur else i + 0.5, max_px=896):
            out.append(f)
    return out


def cleanup_frames(frames: list[Path]) -> None:
    for f in frames:
        if "capframes-" in str(f) or "screenframes-" in str(f):
            f.unlink(missing_ok=True)


def representative_image(row) -> str:
    """The image path downstream stages (tag/describe/faces) should read for a
    row — the poster for a video, the original for a photo."""
    if row["media_kind"] == "video":
        return str(poster_path(row["sha256"]))
    return str(config.LIBRARY_ROOT / row["library_path"])
