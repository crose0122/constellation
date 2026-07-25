"""Stage 6 — Obsidian note generation (SPEC.md §5.6). Notes are a generated,
disposable view of the DB. A <!-- manual --> ... <!-- /manual --> block in an
existing note survives regeneration; everything else is rebuilt."""

import re
from collections import defaultdict
from pathlib import Path

from . import config
from .db import now, start_run, finish_run
from .tag import load_schema

DIM_DIRS = {
    "people": "People", "occasion": "Occasions", "emotion": "Emotions",
    "attire": "Attire", "location": "Locations", "activity": "Activities",
    "time_of_day": "TimeOfDay", "milestone": "Milestones",
    "season_holiday": "Seasons", "group_size": "GroupSize",
    "quality": "Quality", "pets": "Pets", "humor": "Humor",
    "sentiment": "Sentiment", "year": "Years",
}

MANUAL_RE = re.compile(r"<!-- manual -->.*?<!-- /manual -->", re.S)


def _manual_block(path: Path) -> str:
    if path.exists():
        m = MANUAL_RE.search(path.read_text())
        if m:
            return m.group(0)
    return "<!-- manual -->\n<!-- /manual -->"


def _photo_note(photo, tags: dict[str, list[str]], manual: str) -> str:
    name = Path(photo["library_path"]).stem
    lines = ["---", "generated: true", f"photo: {photo['library_path']}"]
    if photo["taken_at"]:
        lines.append(f"taken: {photo['taken_at']}")
    for dim, values in sorted(tags.items()):
        lines.append(f"{dim}: [{', '.join(values)}]")
    lines += ["---", "", f"# {name}", ""]
    lines.append(f"**File:** `{photo['library_path']}`")
    lines += ["", "## Tags", ""]
    for dim, values in sorted(tags.items()):
        d = DIM_DIRS.get(dim, dim.title())
        links = ", ".join(f"[[{d}/{v}|{v}]]" for v in values)
        lines.append(f"- **{d}:** {links}")
    lines += ["", manual, ""]
    return "\n".join(lines)


def generate(conn) -> dict:
    run = start_run(conn, "notes")
    schema = load_schema()

    photos = conn.execute(
        "SELECT * FROM photos WHERE status IN ('tagged','noted')"
    ).fetchall()
    all_tags = defaultdict(lambda: defaultdict(list))
    for row in conn.execute("SELECT photo_id, dimension, value FROM tags"):
        all_tags[row["photo_id"]][row["dimension"]].append(row["value"])

    index = defaultdict(list)  # (dim, value) -> [(note_rel_path, year)]
    stats = {"notes": 0, "index_notes": 0}

    for photo in photos:
        tags = all_tags.get(photo["id"], {})
        year = (photo["taken_at"] or "")[:4] or (tags.get("year") or ["Unknown"])[0]
        note_dir = config.MEMORYVAULT_ROOT / "Photos" / year
        note_dir.mkdir(parents=True, exist_ok=True)
        note_path = note_dir / f"{Path(photo['library_path']).stem}.md"
        note_path.write_text(
            _photo_note(photo, tags, _manual_block(note_path))
        )
        stats["notes"] += 1
        rel = str(note_path.relative_to(config.MEMORYVAULT_ROOT))[:-3]
        for dim, values in tags.items():
            for v in values:
                index[(dim, v)].append((rel, year))
        conn.execute("UPDATE photos SET status = 'noted' WHERE id = ?", (photo["id"],))

    # index notes regenerated wholesale — deterministic, no append drift
    for (dim, value), entries in index.items():
        d = DIM_DIRS.get(dim, dim.title())
        idx_dir = config.MEMORYVAULT_ROOT / d
        idx_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            "---", "generated: true", "type: index",
            f"dimension: {dim}", f"value: {value}", "---",
            "", f"# {value}", "", "## Photos", "",
        ]
        for rel, year in sorted(entries):
            lines.append(f"- [[{rel}]] ({year})")
        (idx_dir / f"{value}.md").write_text("\n".join(lines) + "\n")
        stats["index_notes"] += 1

    conn.commit()
    finish_run(conn, run, stats)
    return stats
