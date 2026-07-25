"""Memory Vault — privacy-first local photo pipeline (SPEC.md)."""

__version__ = "0.1.0"

# iPhone photos are HEIC; Pillow needs the plugin registered once, process-wide,
# so ingest/screen/tag/thumbnails all decode them. Optional: without it HEIC
# files land in the errors queue rather than failing the batch.
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass
