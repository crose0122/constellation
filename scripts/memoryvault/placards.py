"""Gallery placards — one tiny witty museum label per photo.

The wall hangs every picture with a placard plate, like a real gallery.
This sweep writes the labels: qwen gets the photo's verified facts (people
from face recognition, activity/occasion/location tags, geocoded place,
date, and the describe pass's caption) and answers as a fond, slightly
pretentious curator of a family's life. Text-only generation — the vision
work already happened; this is a cheap second pass over its output.

Voice contract (mirrors the product's content rules): warm and dry, never
mean, never explains the joke, names only the people face recognition
named. «Title» — one wry line.
"""
from __future__ import annotations

import json

from . import config
from .db import record_error, start_run, finish_run

MODEL_VERSION = "placard-1.0"

# tag values that describe the archive, not the moment — never placard fodder
_SKIP_VALUES = {"No Pets", "Not Funny", "Important", "Good", "Neutral",
                "Casual", "Solo", "Posing", "None", "Unknown"}
_FACT_DIMS = ("people", "activity", "occasion", "emotion", "location",
              "place", "season_holiday", "time_of_day", "pets")

PROMPT = (
    "You write the tiny placards for a family's home photo gallery — the "
    "little labels next to art in a museum, but for everyday family "
    "photographs, and funny.\n"
    "Given the facts about ONE photo, answer two things:\n"
    '"title": a short invented artwork title, 2-5 words, no quotes.\n'
    '"line": ONE wry line under it — who was doing what, where or when, '
    "at most 12 words.\n"
    "Voice: dry, warm, museum-pretentious about ordinary life ('mixed "
    "media', 'artist unknown', 'circa'). Never mean, never explain the "
    "joke, never invent names — use only the names given, otherwise say "
    "'the artist', 'a small collaborator', etc. Lowercase-leaning; names "
    "and the title keep their capitals."
)

FORMAT = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "line": {"type": "string"}},
    "required": ["title", "line"],
}


def photo_facts(conn, photo_id: int) -> dict:
    """The verified facts a placard may draw on. Pure lookup, no model."""
    facts: dict = {}
    for r in conn.execute(
            "SELECT dimension, value FROM tags WHERE photo_id = ? "
            "AND dimension IN (%s)" % ",".join("?" * len(_FACT_DIMS)),
            (photo_id, *_FACT_DIMS)):
        if r["value"] in _SKIP_VALUES:
            continue
        facts.setdefault(r["dimension"], []).append(r["value"])
    row = conn.execute(
        "SELECT p.taken_at, d.caption FROM photos p "
        "LEFT JOIN descriptions d ON d.photo_id = p.id WHERE p.id = ?",
        (photo_id,)).fetchone()
    if row:
        if row["taken_at"]:
            facts["date"] = row["taken_at"][:10]
        if row["caption"]:
            facts["scene"] = row["caption"]
    return facts


def build_prompt(facts: dict) -> str:
    lines = [PROMPT, "", "Facts:"]
    for key in ("people", "pets", "activity", "occasion", "emotion",
                "location", "place", "season_holiday", "time_of_day"):
        if facts.get(key):
            lines.append(f"  {key.replace('_', '/')}: "
                         + ", ".join(facts[key]))
    if facts.get("date"):
        lines.append(f"  date: {facts['date']}")
    if facts.get("scene"):
        lines.append(f"  what the photo shows: {facts['scene']}")
    return "\n".join(lines)


def render(title: str, line: str) -> str:
    """The stored/displayed form: «Title» — line."""
    title = " ".join(title.strip().strip('"“”«»').split())
    line = " ".join(line.strip().split())
    return f"«{title}» — {line}" if title else line


def _is_placards_cmd(argv: list[str]) -> bool:
    """True for a live `mvault placards` invocation — any interpreter or
    wrapper prefix (venv python, sudo, timeout) — matched on adjacent argv
    tokens. A substring test also matched shells whose -c script or heredoc
    merely mentioned the phrase, and a review agent's own bash proved it."""
    for a, b in zip(argv, argv[1:]):
        if (a == "mvault" or a.endswith("/mvault")) and b == "placards":
            return True
    return False


def _other_sweeps() -> list[str]:
    """Other live `mvault placards` processes. The 2026-07-30 lock storm was
    exactly this: a manual two-shard backfill still running when the nightly
    launched its own sweep — three writers on one library all morning."""
    import os

    me = os.getpid()
    others = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == me:
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                argv = [t.decode(errors="replace")
                        for t in fh.read().split(b"\0") if t]
        except OSError:
            continue  # raced its exit
        if _is_placards_cmd(argv):
            others.append(f"pid {pid}: {' '.join(argv)}")
    return others


def placards(conn, shard: str | None = None, limit: int | None = None,
             force: bool = False) -> dict:
    import sqlite3
    import time as _time

    from .vision_http import post_vision_text

    if not force:
        running = _other_sweeps()
        if running:
            return {"skipped": "another placards sweep is already running — "
                               "rerun with --force for a deliberate overlap "
                               "(sharded backfill)",
                    "running": running}

    def _retry_locked(fn, attempts=6, wait=10):
        # placard shards run alongside the nightly's ingest/tag writers (and
        # each other) — out-wait a writer lock instead of dying on it. Both
        # first-run shards died at 04:4x, mid-nightly, without this.
        for a in range(attempts):
            try:
                return fn()
            except sqlite3.OperationalError as e:
                if "locked" not in str(e) or a == attempts - 1:
                    raise
                _time.sleep(wait)

    def _setup():
        conn.execute(
            "CREATE TABLE IF NOT EXISTS placards ("
            "photo_id INTEGER PRIMARY KEY, placard TEXT, model_version TEXT, "
            "created_at TEXT DEFAULT (datetime('now')))")
        conn.commit()

    _retry_locked(_setup)
    run = _retry_locked(lambda: start_run(conn, "placards"))

    sql = (
        "SELECT id FROM photos "
        "WHERE status IN ('screened','tagged','noted') "
        "AND library_path IS NOT NULL "
        "AND id NOT IN (SELECT photo_id FROM placards) "
        "AND id NOT IN (SELECT photo_id FROM tags WHERE dimension = 'curation' "
        " AND value IN ('Trash','Removed','Delete')) "
    )
    if shard:
        i, m = (int(x) for x in shard.split("/"))
        sql += f"AND id % {m} = {i} "
    sql += "ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()
    stats = {"placarded": 0, "errors": 0}

    for i, row in enumerate(rows, 1):
        try:
            prompt = build_prompt(photo_facts(conn, row["id"]))
            text = post_vision_text(
                {"model": config.VISION_MODEL, "prompt": prompt,
                 "stream": False, "format": FORMAT,
                 "options": {"temperature": 0.9, "num_predict": 80}},
                timeout=90)
            if "```" in text:
                text = (text.split("```json")[-1].split("```")[0]
                        if "```json" in text else text.split("```")[1])
            raw = json.loads(text.strip())
            placard = render(str(raw.get("title", "")), str(raw.get("line", "")))
            if not placard:
                raise ValueError("empty placard")
            _retry_locked(lambda: conn.execute(
                "INSERT OR REPLACE INTO placards "
                "(photo_id, placard, model_version) VALUES (?,?,?)",
                (row["id"], placard, MODEL_VERSION)))
            stats["placarded"] += 1
        except Exception as e:
            stats["errors"] += 1
            _retry_locked(lambda: record_error(
                conn, "placards", repr(e), photo_id=row["id"]))
        _retry_locked(conn.commit)
        if i % 200 == 0:
            print(f"  {i}/{len(rows)} placarded, "
                  f"{stats['errors']} errors", flush=True)

    _retry_locked(lambda: finish_run(conn, run, stats))
    return stats
