"""Screening threshold calibration (SPEC.md §12.1) — the M4 gate.

Point it at two labeled folders (safe family photos, and a flagged set) and
it sweeps the pass-1 threshold, printing the recall/false-positive table a
human approves before any real sweep. Photos never leave the machine; only
aggregate numbers are printed.
"""

from pathlib import Path

from . import config


def collect_scores(directory: Path, score_fn) -> tuple[list[float], list[str]]:
    scores, failures = [], []
    for path in sorted(directory.rglob("*")):
        if path.suffix.lower() not in config.IMAGE_EXTENSIONS:
            continue
        try:
            scores.append(score_fn(str(path)))
        except Exception as e:
            failures.append(f"{path.name}: {e}")
    return scores, failures


def sweep(safe_scores: list[float], flagged_scores: list[float],
          thresholds=None) -> list[dict]:
    thresholds = thresholds or [round(t * 0.05, 2) for t in range(1, 20)]
    rows = []
    for t in thresholds:
        caught = sum(1 for s in flagged_scores if s >= t)
        false_pos = sum(1 for s in safe_scores if s >= t)
        rows.append({
            "threshold": t,
            "recall": caught / len(flagged_scores) if flagged_scores else 0.0,
            "false_positive_rate": false_pos / len(safe_scores) if safe_scores else 0.0,
            "caught": caught,
            "false_positives": false_pos,
        })
    return rows


def recommend(rows: list[dict], target_recall: float = 0.99) -> dict | None:
    """Highest threshold that still meets the recall target — fewest safe
    photos sent to pass 2 while keeping the catch rate."""
    ok = [r for r in rows if r["recall"] >= target_recall]
    return max(ok, key=lambda r: r["threshold"]) if ok else None


def calibrate(safe_dir: Path, flagged_dir: Path, score_fn=None,
              target_recall: float = 0.99) -> dict | None:
    if score_fn is None:
        from .screen import pass1_score

        score_fn = pass1_score

    safe_scores, safe_fail = collect_scores(safe_dir, score_fn)
    flagged_scores, flagged_fail = collect_scores(flagged_dir, score_fn)
    print(f"scored {len(safe_scores)} safe, {len(flagged_scores)} flagged"
          + (f" ({len(safe_fail) + len(flagged_fail)} unreadable)" if safe_fail or flagged_fail else ""))
    if not safe_scores or not flagged_scores:
        print("need at least one scored photo in each folder")
        return None

    rows = sweep(safe_scores, flagged_scores)
    print(f"\n{'T_low':>6} {'recall':>8} {'FP rate':>8} {'caught':>7} {'false+':>7}")
    for r in rows:
        print(f"{r['threshold']:>6.2f} {r['recall']:>8.1%} "
              f"{r['false_positive_rate']:>8.1%} {r['caught']:>7} {r['false_positives']:>7}")

    rec = recommend(rows, target_recall)
    if rec:
        print(f"\nrecommended T_low = {rec['threshold']:.2f} "
              f"(recall {rec['recall']:.1%}, sends {rec['false_positive_rate']:.1%} "
              f"of safe photos to pass-2 confirmation)")
        print(f"apply with: export MEMORYVAULT_SCREEN_T_LOW={rec['threshold']:.2f}")
    else:
        print(f"\nNO threshold reaches {target_recall:.0%} recall — the pass-1 "
              "classifier is not good enough; do not run the sweep with it.")
    return rec
