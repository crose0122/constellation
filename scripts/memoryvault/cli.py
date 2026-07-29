"""mvault — the Memory Vault CLI (SPEC.md §8)."""

import argparse
import sys
from pathlib import Path

from . import config, db


def _conn():
    return db.init(config.DB_PATH)


def _apply_library_override(args):
    if getattr(args, "library", None):
        config.LIBRARY_ROOT = Path(args.library)
        config.DB_PATH = config.LIBRARY_ROOT / "photos.db"


def cmd_init(args):
    for d in config.library_dirs().values():
        d.mkdir(parents=True, exist_ok=True)
    db.init(config.DB_PATH).close()
    print(f"initialized {config.LIBRARY_ROOT} (db: {config.DB_PATH})")


def cmd_discover(args):
    from .discover import discover

    with _conn() as conn:
        stats = discover(conn, Path(args.root), kind=args.kind,
                         description=args.description or "")
    print(stats)


def cmd_ingest(args):
    from .ingest import ingest

    with _conn() as conn:
        print(ingest(conn, limit=args.limit, sample=args.sample))


def cmd_dedup(args):
    from .dedup import dedup

    with _conn() as conn:
        print(dedup(conn, threshold=args.threshold, quarantine=args.quarantine))


def cmd_screen(args):
    from .screen import rescreen, screen

    with _conn() as conn:
        if getattr(args, "rescreen", False):
            print(rescreen(conn, shard=args.shard, limit=args.limit))
        else:
            print(screen(conn))


def cmd_tag(args):
    from .tag import tag

    with _conn() as conn:
        print(tag(conn, limit=args.limit, shard=args.shard,
                  retag=getattr(args, "retag", False)))


def cmd_describe(args):
    from .describe import describe

    with _conn() as conn:
        print(describe(conn, shard=args.shard, limit=args.limit))


def cmd_notes(args):
    from .notes import generate

    with _conn() as conn:
        print(generate(conn))


def cmd_edges(args):
    from .edges import compute_edges

    with _conn() as conn:
        print(compute_edges(conn))


def cmd_status(args):
    with _conn() as conn:
        f = db.funnel(conn)
    width = max(len(k) for k in f)
    for k, v in f.items():
        print(f"  {k:<{width}}  {v}")


def cmd_geocode(args):
    from .geocode import geocode

    with _conn() as conn:
        print(geocode(conn))


def cmd_faces(args):
    from .faces import cluster, scan

    with _conn() as conn:
        if args.action == "scan":
            print(scan(conn, limit=args.limit))
        else:
            print(cluster(conn))


def cmd_curate(args):
    from .curate import curate, live_photos, rescue, screenshots, vision_docs

    with _conn() as conn:
        if getattr(args, "vision_docs", False):
            print(vision_docs(conn, shard=args.shard, limit=args.limit))
        elif getattr(args, "screenshots", False):
            print(screenshots(conn, shard=args.shard, limit=args.limit))
        elif getattr(args, "rescue", False):
            print(rescue(conn, shard=args.shard, limit=args.limit))
        elif getattr(args, "live_photos", False):
            print(live_photos(conn))
        else:
            print(curate(conn))


def cmd_retry(args):
    """Errors are retried by re-running their stage: errored files stay
    'discovered', errored photos stay 'staged'/'screened', so the stages
    naturally re-select them. Old error rows get marked resolved."""
    from .ingest import ingest
    from .tag import tag

    with _conn() as conn:
        conn.execute("UPDATE errors SET resolved = 1 WHERE resolved = 0")
        conn.commit()
        print("ingest:", ingest(conn))
        try:
            from .screen import screen

            print("screen:", screen(conn))
        except Exception as e:
            print(f"screen skipped: {e}")
        print("tag:", tag(conn))


def cmd_review(args):
    with _conn() as conn:
        if args.approve_group:
            conn.execute(
                "UPDATE duplicate_members SET decision='discard-approved' "
                "WHERE group_id = ? AND decision='pending'",
                (args.approve_group,),
            )
            conn.commit()
            print(f"group {args.approve_group} approved for discard "
                  "(files remain in duplicates/ until purge)")
            return
        rows = conn.execute(
            "SELECT g.id, g.kind, g.keeper_photo_id, COUNT(m.file_id) AS pending "
            "FROM duplicate_groups g JOIN duplicate_members m ON m.group_id = g.id "
            "WHERE m.decision = 'pending' GROUP BY g.id"
        ).fetchall()
        if not rows:
            print("no pending duplicate reviews")
        for r in rows:
            print(f"  group {r['id']} ({r['kind']}): keeper photo "
                  f"{r['keeper_photo_id']}, {r['pending']} pending")


def cmd_vault(args):
    from . import vault

    if args.action == "create":
        vault.create_vault(size_gb=args.size)
    elif args.action == "open":
        vault.open_vault()
        with _conn() as conn:
            n = vault.backfill_ledger(conn)
        if n:
            print(f"vaulted-sha ledger: {n} entries backfilled")
    elif args.action == "close":
        vault.close_vault()
    elif args.action == "status":
        print("mounted" if vault.is_mounted() else "not mounted")
    elif args.action == "review":
        # The screener routes anything it can't call confidently into
        # vault/review. Only a human clears that queue — these three verdicts
        # existed in vault.py but were reachable from nothing until now.
        if not vault.is_mounted():
            print("vault is locked — run: mvault vault open")
            return
        rdir = config.VAULT_MOUNT / "review"
        names = sorted(f.name for f in rdir.iterdir() if f.is_file()) \
            if rdir.is_dir() else []
        if args.release or args.keep or args.delete:
            with _conn() as conn:
                for fn in (args.release or []):
                    print(vault.release_from_review(conn, fn))
                for fn in (args.keep or []):
                    print(vault.keep_in_vault(fn))
                for fn in (args.delete or []):
                    print(vault.delete_from_review(fn))
            return
        print(f"{len(names)} photo(s) awaiting review in {rdir}")
        for n in names:
            print("  ", n)


def cmd_calibrate(args):
    from .calibrate import calibrate

    calibrate(Path(args.safe), Path(args.flagged),
              target_recall=args.target_recall)


def cmd_migrate_quarantine(args):
    from .migrate import migrate_quarantine

    migrate_quarantine(Path(args.quarantine_dir) if args.quarantine_dir else None)


def cmd_brain(args):
    from .brain.server import serve

    serve(host=args.host, port=args.port)


def cmd_demo(args):
    from .demo import build_demo
    from .brain.server import serve

    target = Path(args.dir) if args.dir else config.LIBRARY_ROOT / "demo"
    build_demo(target, n=args.n)
    if not args.no_serve:
        serve(host=args.host, port=args.port, db_path=config.DB_PATH)


def main(argv=None):
    p = argparse.ArgumentParser(prog="mvault", description="Memory Vault pipeline")
    p.add_argument("--library", help="override library root (default from config/env)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create library dirs + db")

    d = sub.add_parser("discover", help="scan a source root (read-only)")
    d.add_argument("root")
    d.add_argument("--kind", default="local",
                   choices=["local", "usb", "smb", "phone-export"])
    d.add_argument("--description")

    i = sub.add_parser("ingest", help="hash/EXIF/stage discovered files")
    i.add_argument("--limit", type=int)
    i.add_argument("--sample", action="store_true",
                   help="random order (for the M1.5 sample)")

    dd = sub.add_parser("dedup", help="near-duplicate detection")
    dd.add_argument("--threshold", type=int)
    dd.add_argument("--quarantine", action="store_true")

    sc = sub.add_parser("screen", help="two-pass explicit-content screening")
    sc.add_argument("--rescreen", action="store_true",
                    help="re-sweep the already-visible library with the "
                         "current thresholds")
    sc.add_argument("--shard")
    sc.add_argument("--limit", type=int)

    t = sub.add_parser("tag", help="vision tagging of screened photos")
    t.add_argument("--limit", type=int)
    t.add_argument("--shard", help="i/m: process ids where id %% m == i "
                   "(disjoint multi-worker split)")
    t.add_argument("--retag", action="store_true",
                   help="re-tag already-tagged photos under the current "
                        "schema version (replaces old vision tags only)")

    de = sub.add_parser("describe",
                        help="caption + OCR + orientation pass (GPU)")
    de.add_argument("--shard")
    de.add_argument("--limit", type=int)

    sub.add_parser("notes", help="generate Obsidian notes from the db")
    sub.add_parser("geocode", help="GPS EXIF -> place tags (offline dataset)")
    sub.add_parser("edges", help="compute memory-graph edges")
    sub.add_parser("status", help="pipeline funnel")
    sub.add_parser("retry", help="re-run stages for errored items")
    cu = sub.add_parser("curate", help="heuristic Trash tagging (tiny/screenshot/cache)")
    cu.add_argument("--vision-docs", action="store_true", dest="vision_docs",
                    help="GPU pass: bin photographed paperwork/documents")
    cu.add_argument("--screenshots", action="store_true",
                    help="GPU pass: re-judge still-visible screenshots, bin "
                         "the ones that are texts/receipts/documents and "
                         "leave screenshots of real photos alone")
    cu.add_argument("--rescue", action="store_true",
                    help="GPU pass: model-review the Trash bin, flip real "
                         "photos to Kept")
    cu.add_argument("--live-photos", action="store_true", dest="live_photos",
                    help="hide iPhone Live Photo motion clips (kept, restorable)")
    cu.add_argument("--shard")
    cu.add_argument("--limit", type=int)
    fa = sub.add_parser("faces", help="face detection + clustering (InsightFace)")
    fa.add_argument("action", choices=["scan", "cluster"])
    fa.add_argument("--limit", type=int)

    r = sub.add_parser("review", help="duplicate review queue")
    r.add_argument("--approve-group", type=int)

    v = sub.add_parser("vault", help="LUKS vault management")
    v.add_argument("action",
                   choices=["create", "open", "close", "status", "review"])
    v.add_argument("--size", type=int, default=50, help="GB (create only)")
    v.add_argument("--release", nargs="*", metavar="FILE",
                   help="review verdict: it's fine — back into the library")
    v.add_argument("--keep", nargs="*", metavar="FILE",
                   help="review verdict: it belongs in the vault")
    v.add_argument("--delete", nargs="*", metavar="FILE",
                   help="review verdict: garbage — shred it (no undo)")

    c = sub.add_parser("calibrate", help="sweep screening thresholds on labeled samples")
    c.add_argument("--safe", required=True, help="folder of known-safe photos")
    c.add_argument("--flagged", required=True, help="folder of flagged-set photos")
    c.add_argument("--target-recall", type=float, default=0.99)

    mq = sub.add_parser("migrate-quarantine",
                        help="move legacy plaintext Quarantine/ into the vault and shred")
    mq.add_argument("--quarantine-dir")

    b = sub.add_parser("brain", help="serve The Brain web UI")
    b.add_argument("--host", default="0.0.0.0")
    b.add_argument("--port", type=int, default=8484)

    dm = sub.add_parser("demo", help="build + serve a synthetic demo library")
    dm.add_argument("--dir")
    dm.add_argument("--n", type=int, default=60)
    dm.add_argument("--host", default="0.0.0.0")
    dm.add_argument("--port", type=int, default=8484)
    dm.add_argument("--no-serve", action="store_true")

    args = p.parse_args(argv)
    _apply_library_override(args)
    {
        "init": cmd_init, "discover": cmd_discover, "ingest": cmd_ingest,
        "dedup": cmd_dedup, "screen": cmd_screen, "tag": cmd_tag,
        "notes": cmd_notes, "edges": cmd_edges, "status": cmd_status,
        "retry": cmd_retry, "review": cmd_review, "vault": cmd_vault,
        "curate": cmd_curate, "faces": cmd_faces, "geocode": cmd_geocode,
        "describe": cmd_describe,
        "calibrate": cmd_calibrate, "migrate-quarantine": cmd_migrate_quarantine,
        "brain": cmd_brain, "demo": cmd_demo,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
