#!/usr/bin/env python3
"""PyInstaller entry point for the bundled Constellation backend.

Ships as `memoryvault-brain(.exe)`. Runs the mvault CLI, so the same binary
serves the web UI (`memoryvault-brain brain`) and runs every pipeline stage
(`memoryvault-brain tag`, `... screen`, etc.) — no Python or Docker needed on
the target machine.
"""
import os
import sys


def _bundle_dir():
    # PyInstaller unpacks data next to the exe (onedir) or into _MEIPASS
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def _wire_bundled_paths():
    base = _bundle_dir()
    # the tag schema travels with the app unless the installer set a path
    if not os.environ.get("MEMORYVAULT_TAG_SCHEMA"):
        cand = os.path.join(base, "schema", "tag-schema.json")
        if os.path.exists(cand):
            os.environ["MEMORYVAULT_TAG_SCHEMA"] = cand


if __name__ == "__main__":
    _wire_bundled_paths()
    from memoryvault.cli import main
    main()
