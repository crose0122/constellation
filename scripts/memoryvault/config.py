"""Single config surface (SPEC.md §7). Every value is env-overridable;
no other module hardcodes a path, URL, or model name."""

import os
from pathlib import Path


def _path(env: str, default: str) -> Path:
    return Path(os.environ.get(env, default)).expanduser()


# Obsidian vault (human-facing, generated view)
MEMORYVAULT_ROOT = _path("MEMORYVAULT_ROOT", "~/memory-vault")

# Photo library (machine side, system of record)
LIBRARY_ROOT = _path("MEMORYVAULT_LIBRARY_ROOT", "~/Constellation/library")
DB_PATH = _path("MEMORYVAULT_DB_PATH", str(LIBRARY_ROOT / "photos.db"))

# LUKS vault
VAULT_IMG = _path("MEMORYVAULT_VAULT_IMG", str(LIBRARY_ROOT / "vault.img"))
VAULT_MOUNT = _path("MEMORYVAULT_VAULT_MOUNT", "~/Constellation/vault")
# "luks" (default, Linux): flagged content lives in an encrypted LUKS image a
# human unlocks. "dir": VAULT_MOUNT is a plain directory treated as always-
# available — for Windows/containers where the HOST disk is already encrypted.
VAULT_MODE = os.environ.get("MEMORYVAULT_VAULT_MODE", "luks")

# Inference (local Ollama by default)
OLLAMA_URL = os.environ.get(
    "MEMORYVAULT_OLLAMA_URL", "http://127.0.0.1:11434/api/generate"
)
# 7b: the 3b emits degenerate '?' streams on image+prompt under ollama >= 0.31
# (grammar stack exception with format=json); 7b is stable and was the target
# model anyway once GPU headroom allowed (SPEC §7). Needs ~7-8GB VRAM.
VISION_MODEL = os.environ.get("MEMORYVAULT_VISION_MODEL", "qwen2.5vl:7b")
# Local weights dir for the pass-1 NSFW classifier (downloaded once, offline after)
NSFW_MODEL_PATH = os.environ.get("MEMORYVAULT_NSFW_MODEL_PATH", "")

# Screening thresholds (SPEC.md §5.4; calibrate before the real sweep)
# t_low 0.20 -> 0.05 (2026-07-24): NSFW misses reached the open library —
# below t_low the vision model never looks, so pass-1 false negatives sailed
# through. 0.05 sends far more photos to the qwen confirm, which is cheap.
SCREEN_T_LOW = float(os.environ.get("MEMORYVAULT_SCREEN_T_LOW", "0.05"))
SCREEN_T_HIGH = float(os.environ.get("MEMORYVAULT_SCREEN_T_HIGH", "0.85"))

# Dedup
NEAR_DUP_THRESHOLD = int(os.environ.get("MEMORYVAULT_NEAR_DUP_THRESHOLD", "10"))

# Email sharing (the Brain's per-photo share button). Credentials live in an
# environment file / your host config, never in the repo.
SMTP_HOST = os.environ.get("MEMORYVAULT_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("MEMORYVAULT_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("MEMORYVAULT_SMTP_USER", "")
SMTP_PASS = os.environ.get("MEMORYVAULT_SMTP_PASS", "")
SHARE_FROM = os.environ.get("MEMORYVAULT_SHARE_FROM", SMTP_USER)

# Tag vocabulary
TAG_SCHEMA_PATH = _path(
    "MEMORYVAULT_TAG_SCHEMA",
    str(Path(__file__).resolve().parent.parent.parent / "schema" / "tag-schema.json"),
)

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp", ".heic", ".heif",
    ".raw", ".cr2", ".nef",
}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".3gp", ".webm", ".mts"}


def library_dirs() -> dict[str, Path]:
    return {
        "staging": LIBRARY_ROOT / "staging",
        "originals": LIBRARY_ROOT / "originals",
        "duplicates": LIBRARY_ROOT / "duplicates",
        "thumbnails": LIBRARY_ROOT / "thumbnails",
    }
