"""Brand artwork generation is reproducible from repository-owned inputs."""

import hashlib
import importlib.util
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
GENERATOR = REPO / "scripts" / "tools" / "make_launcher_art.py"
FONT_HASHES = {
    "Lato-Light.ttf": "e9d59afb6d9cb9cf6e8d8159d4639d5b577e29efc64a15182f228659cfc1e818",
    "Lato-Semibold.ttf": "2dc5d31e2cf1e29f3430eb2dfa1ba9911e08ee401b61dd12f40e0acb047a17a3",
}
BANNER_PIXELS_SHA256 = "0bfb3381e3f5a8b8afe2bb872c4eec742e2b096f944ebefbd05b5b9550ba4bfb"


def test_launcher_art_uses_pinned_repo_fonts_and_renders_deterministically():
    assert GENERATOR.is_file(), "the branding generator must be versioned"
    spec = importlib.util.spec_from_file_location("make_launcher_art", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    font_dir = REPO / "scripts" / "tools" / "assets" / "fonts" / "lato"
    assert Path(module.LATO.format("Light")) == font_dir / "Lato-Light.ttf"
    assert Path(module.LATO.format("Semibold")) == font_dir / "Lato-Semibold.ttf"
    for name, expected in FONT_HASHES.items():
        assert hashlib.sha256((font_dir / name).read_bytes()).hexdigest() == expected

    first = module.banner().tobytes()
    second = module.banner().tobytes()
    assert first == second
    assert hashlib.sha256(first).hexdigest() == BANNER_PIXELS_SHA256
