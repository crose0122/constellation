"""Stage 4 — Two-pass explicit-content screening (SPEC.md §5.4).

Invariants enforced here:
  * runs BEFORE tagging — flagged items never get a caption/tag/embedding
  * an infrastructure error is NEVER a verdict (fail-safe: errors table)
  * the vault must be mounted before a batch starts; flagged items must
    have somewhere safe to go, queueing them in plaintext is not an option
"""

import base64
from pathlib import Path

import sqlite3

from . import config
from .db import record_error, start_run, finish_run
from . import vault

SAFE, VAULT, REVIEW, ERROR = "safe", "vault", "review", "error"


class ScreenError(Exception):
    pass


# ---------------------------------------------------------------- pass 1

_classifier = None


def _load_classifier():
    """Fast local NSFW classifier. Tries opennsfw2, then a local
    transformers checkpoint (Falconsai/nsfw_image_detection weights
    downloaded once to NSFW_MODEL_PATH). Raises ScreenError if neither
    is available — which routes photos to the error queue, never to a verdict."""
    global _classifier
    if _classifier is not None:
        return _classifier
    try:
        import opennsfw2

        model = opennsfw2.make_open_nsfw_model()

        def score(path: str) -> float:
            img = opennsfw2.preprocess_image(
                opennsfw2.Image.open(path), opennsfw2.Preprocessing.YAHOO
            )
            import numpy as np

            return float(model.predict(np.expand_dims(img, 0), verbose=0)[0][1])

        _classifier = score
        return _classifier
    except ImportError:
        pass
    try:
        from transformers import pipeline

        model_ref = config.NSFW_MODEL_PATH or "Falconsai/nsfw_image_detection"
        pipe = pipeline("image-classification", model=model_ref)

        def score(path: str) -> float:
            for out in pipe(path):
                if out["label"].lower() == "nsfw":
                    return float(out["score"])
            return 0.0

        _classifier = score
        return _classifier
    except ImportError:
        raise ScreenError(
            "no NSFW classifier available (install opennsfw2 or transformers, "
            "and set MEMORYVAULT_NSFW_MODEL_PATH to local weights)"
        )


def pass1_score(path: str) -> float:
    try:
        return _load_classifier()(path)
    except ScreenError:
        raise
    except Exception as e:
        raise ScreenError(f"pass-1 classifier failed: {e!r}")


# ---------------------------------------------------------------- pass 2

_CONFIRM_PROMPT = (
    "Answer with exactly one word. Does this image contain nudity, explicit, "
    "adult, or sexual content? Answer 'yes' or 'no'."
)


def confirm_explicit(path: str) -> bool:
    """qwen2.5vl yes/no confirmation. No caption is requested or stored."""
    import requests

    try:
        from .tag import model_image_b64

        image_b64 = model_image_b64(path)
        from .vision_http import post_vision_text
        answer = post_vision_text({
            "model": config.VISION_MODEL,
            "prompt": _CONFIRM_PROMPT,
            "images": [image_b64],
            "stream": False,
        }, timeout=60).strip().lower()
        if answer.startswith("yes"):
            return True
        if answer.startswith("no"):
            return False
        raise ScreenError(f"unparseable pass-2 answer: {answer[:80]!r}")
    except ScreenError:
        raise
    except Exception as e:
        raise ScreenError(f"pass-2 confirmation failed: {e!r}")


# ---------------------------------------------------------------- verdict

def screen_verdict(
    path: str,
    score_fn=pass1_score,
    confirm_fn=confirm_explicit,
    t_low: float | None = None,
    t_high: float | None = None,
) -> tuple[str, float | None]:
    """Pure decision logic (unit-testable with fake classifiers).
    Returns (verdict, pass1_score)."""
    t_low = config.SCREEN_T_LOW if t_low is None else t_low
    t_high = config.SCREEN_T_HIGH if t_high is None else t_high
    try:
        s = score_fn(path)
    except ScreenError as e:
        return ERROR, None
    if s < t_low:
        return SAFE, s
    try:
        explicit = confirm_fn(path)
    except ScreenError:
        return ERROR, s
    if explicit:
        return VAULT, s
    if s >= t_high:
        return REVIEW, s  # classifiers disagree hard → human call
    return SAFE, s


def verdict_for_row(row, score_fn=pass1_score, confirm_fn=confirm_explicit):
    """Media-aware verdict. Photos: screen the image. Videos: screen SEVERAL
    sampled frames and take the worst verdict (a tame poster must never let an
    explicit clip through) — VAULT beats REVIEW beats SAFE; a bare ERROR only
    if no frame produced a real verdict. Returns (verdict, score)."""
    if row["media_kind"] != "video":
        return screen_verdict(str(config.LIBRARY_ROOT / row["library_path"]),
                              score_fn, confirm_fn)
    from . import video as vid
    frames = vid.sample_frame_paths(
        str(config.LIBRARY_ROOT / row["library_path"]), row["sha256"],
        row["duration"] if "duration" in row.keys() else None)
    if not frames:
        return ERROR, None
    worst, worst_score, saw_ok = ERROR, None, False
    rank = {VAULT: 3, REVIEW: 2, SAFE: 1, ERROR: 0}
    for f in frames:
        v, s = screen_verdict(str(f), score_fn, confirm_fn)
        if v != ERROR:
            saw_ok = True
        if rank[v] > rank[worst]:
            worst, worst_score = v, s
    # clean the temp screening frames (poster stays — it's the display image)
    for f in frames:
        if "screenframes-" in str(f):
            f.unlink(missing_ok=True)
    return (worst if saw_ok else ERROR), worst_score


def screen(conn, score_fn=pass1_score, confirm_fn=confirm_explicit) -> dict:
    """Screen every staged photo/video. Halts up front if vault unavailable."""
    if not vault.is_mounted():
        raise vault.VaultUnavailable(
            f"vault not mounted at {config.VAULT_MOUNT}; run vault-open first "
            "(flagged items must have somewhere safe to go)"
        )

    run = start_run(conn, "screen")
    rows = conn.execute("SELECT * FROM photos WHERE status = 'staged'").fetchall()
    stats = {SAFE: 0, VAULT: 0, REVIEW: 0, ERROR: 0}

    # A write here used to be bare, while rescreen() next door already
    # retried on a locked database. A concurrent stage held the lock, this
    # raised, and the whole pass died after screening nothing — leaving 241
    # unscreened items on display because the calling script logged the error
    # and carried on. Screening is the one stage that must not fail quietly.
    def _retry_locked(fn, attempts=8, wait=8):
        import time as _t

        for i in range(attempts):
            try:
                return fn()
            except sqlite3.OperationalError as e:
                if "locked" not in str(e).lower() or i == attempts - 1:
                    raise
                _t.sleep(wait)

    for row in rows:
        try:
            verdict, score = verdict_for_row(row, score_fn, confirm_fn)
            stats[verdict] += 1
            if verdict == SAFE:
                _retry_locked(lambda: conn.execute(
                    "UPDATE photos SET status = 'screened', screen_score = ? "
                    "WHERE id = ?", (score, row["id"])))
            elif verdict == VAULT:
                _retry_locked(lambda: vault.route_to_vault(
                    conn, row["id"], review=False))
            elif verdict == REVIEW:
                _retry_locked(lambda: vault.route_to_vault(
                    conn, row["id"], review=True))
            else:  # ERROR — fail safe: photo stays put, retried later
                record_error(conn, "screen", "screening error (see logs)",
                             photo_id=row["id"])
            _retry_locked(conn.commit)
        except Exception as e:
            # One unscreenable photo must not strand the rest. It keeps
            # status='staged', so the next run picks it up again.
            stats[ERROR] += 1
            try:
                record_error(conn, "screen", repr(e), photo_id=row["id"])
                conn.commit()
            except Exception:
                pass

    finish_run(conn, run, stats)
    return stats


def rescreen(conn, score_fn=pass1_score, confirm_fn=confirm_explicit,
             shard: str | None = None, limit: int | None = None) -> dict:
    """Sweep the ALREADY-VISIBLE library with the current (stricter)
    thresholds. Exists because the original pass auto-safed everything the
    pass-1 classifier scored under 0.20 — its false negatives were never
    shown to the vision model at all. Same verdicts as screen(): explicit ->
    vault, hard disagreement -> vault review, safe -> stamped (screen_check)
    so nightly re-runs only sweep photos not yet re-checked."""
    if not vault.is_mounted():
        raise vault.VaultUnavailable(
            f"vault not mounted at {config.VAULT_MOUNT}; run vault-open first "
            "(flagged items must have somewhere safe to go)"
        )

    run = start_run(conn, "rescreen")
    sql = (
        "SELECT * FROM photos WHERE status IN ('screened','tagged','noted') "
        "AND library_path IS NOT NULL AND id NOT IN "
        "(SELECT photo_id FROM tags WHERE dimension = 'screen_check') "
    )
    if shard:
        i, m = (int(x) for x in shard.split("/"))
        sql += f"AND id % {m} = {i} "
    sql += "ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()
    stats = {SAFE: 0, VAULT: 0, REVIEW: 0, ERROR: 0}

    import sqlite3
    import time as _time

    def _retry_locked(fn, attempts=6, wait=10):
        # rescreen runs alongside the describe/tag writers; out-wait the writer
        # lock instead of dying on it (mirrors tag.py's guard)
        for a in range(attempts):
            try:
                return fn()
            except sqlite3.OperationalError as e:
                if "locked" not in str(e) or a == attempts - 1:
                    raise
                _time.sleep(wait)

    for i, row in enumerate(rows, 1):
        verdict, score = verdict_for_row(row, score_fn, confirm_fn)
        stats[verdict] += 1

        def _write():
            if verdict == SAFE:
                conn.execute(
                    "INSERT OR IGNORE INTO tags (photo_id, dimension, value, "
                    "confidence, model_version) VALUES "
                    "(?, 'screen_check', 'rescreen-passed', 1.0, 'rescreen-1.0')",
                    (row["id"],),
                )
            elif verdict == VAULT:
                vault.route_to_vault(conn, row["id"], review=False)
            elif verdict == REVIEW:
                vault.route_to_vault(conn, row["id"], review=True)
            else:  # ERROR — fail safe: no verdict recorded, retried next sweep
                record_error(conn, "rescreen", "rescreen error (see logs)",
                             photo_id=row["id"])
            conn.commit()

        _retry_locked(_write)
        if i % 200 == 0:
            print(f"  {i}/{len(rows)} rescreened, "
                  f"{stats[VAULT]} vaulted, {stats[REVIEW]} to review",
                  flush=True)

    finish_run(conn, run, stats)
    return stats
