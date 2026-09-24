"""File formats at ingest (V2 CP3, spec B5).

- HEIC/HEIF (iPhone, incl. the still half of a Live Photo): the original is
  kept byte-for-byte in originals/; a JPEG rendition is made at ingest and
  becomes the photo's working file. Every later stage — screening, tagging,
  faces (OpenCV can't read HEIC), browsers — reads a JPEG.
- RAW (CR2/CR3/NEF/ARW/DNG/ORF/RW2/RAF/RAW): archive-only. Recognized and
  stored under archive/raw/, never shown in the family stream.
- Anything over MAX_FILE_BYTES (4 GB) is not copied; it's recorded with a
  plain-words reason so the family knows why it's missing.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps

from . import config

HEIC_EXTENSIONS = {".heic", ".heif"}
RAW_EXTENSIONS = {".raw", ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".dng",
                  ".orf", ".rw2", ".raf", ".pef", ".srw"}

JPEG_QUALITY = 92
EXIF_ORIENTATION = 0x0112


def kind_of(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in HEIC_EXTENSIONS:
        return "heic"
    if ext in RAW_EXTENSIONS:
        return "raw"
    return "image"


def too_large(size_bytes: int | None) -> bool:
    return size_bytes is not None and size_bytes > config.MAX_FILE_BYTES


def _human(n: int) -> str:
    for unit, div in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if n >= div:
            v = n / div
            return f"{v:.1f} {unit}" if v < 10 else f"{v:.0f} {unit}"
    return f"{n} bytes"


def too_large_message(path: Path, size_bytes: int) -> str:
    return (f"{path.name} is {_human(size_bytes)}. Constellation skips files over "
            f"{_human(config.MAX_FILE_BYTES)} (usually a very long video); the original is untouched.")


def heic_to_jpeg(src: Path, dest: Path) -> dict:
    """Write a JPEG rendition of a HEIC. Orientation is baked into the pixels
    (and the tag reset), everything else in EXIF — date, camera, GPS — is
    carried over so the JPEG says what the original says."""
    with Image.open(src) as im:
        exif = im.getexif()
        upright = ImageOps.exif_transpose(im)
        rgb = upright.convert("RGB")      # 10-bit / alpha / P modes -> 8-bit RGB
    if EXIF_ORIENTATION in exif:
        exif[EXIF_ORIENTATION] = 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".jpg.part")
    rgb.save(tmp, "JPEG", quality=JPEG_QUALITY, exif=exif.tobytes(), optimize=True)
    tmp.replace(dest)
    return {"width": rgb.width, "height": rgb.height}


def raw_exif(src: Path) -> dict:
    """Best effort: many RAWs are TIFF containers Pillow can read tags from.
    A RAW Pillow can't open is still archived, just without a date."""
    from .ingest import extract_exif

    try:
        with Image.open(src) as im:
            return extract_exif(im)
    except Exception:
        return {"taken_at": None, "camera": None, "gps_lat": None, "gps_lon": None}


def jpeg_bytes_exif(path: Path) -> dict:
    """For tests/tools: the EXIF of a written JPEG, via the same reader ingest uses."""
    from .ingest import extract_exif

    with Image.open(io.BytesIO(Path(path).read_bytes())) as im:
        return extract_exif(im)
