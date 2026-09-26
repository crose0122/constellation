#!/usr/bin/env python3
"""Leak scan for the public Constellation repo.

Nothing household-specific may be committed: real names, home IPs, private
hostnames, real home paths. This scans every tracked text file (or the paths
given) and fails on any hit. Runs in CI and in .githooks/pre-push.

The forbidden terms themselves are stored as SHA-256 hashes of lowercase
words, so this file doesn't publish the very names it protects. Add a term:
    python3 tools/leak_scan.py --hash someword
and paste the hash into WORD_HASHES. Structural patterns (IPs, paths) are
plain regexes — they aren't secret.

    tools/leak_scan.py                 # scan all tracked files
    tools/leak_scan.py FILE [FILE...]  # scan specific files
    tools/leak_scan.py --messages A..B # scan commit messages in range A..B
"""
import hashlib
import re
import subprocess
import sys
from pathlib import Path

# sha256(lowercase word) of household names / private identifiers
WORD_HASHES = {
    "5d7f15f2fce8ddb2dbef5c38be896c238ba7e0a432e396759030a853fa6b1151",
    "972964b66bdfe6b5b181c5112a6a0470204f64661ee7e3efb9aab0ce3cc403ff",
    "e0e3a2b6471d044a53a7757994b51dd33c6b3ec90e1aca21cebc8e2ae79d6d9b",
    "e9a63a4eb15738ae85cd416221c8fcc4ccc0018fac91335b42eaa016c76e87f9",
    "270d76c78b081db72f35458cc7b0019ced8e29da7ec15a77d99533a0dcb06c1d",
    "52610e3505010decd3118b58719bfd56409fa76da23ef010d04ab4c4931c4978",
    "eeac7816005ff0bde67004b95a5563844d60901f3db3baaafc62ac4e850e08de",
    "0357513deb903a056e74a7e475247fc1ffe31d8be4c1d4a31f58dd47ae484100",
    "09d2b64c387acc00d5aa7627700e6b3ecd4b896696acd6d3528b5fd07374c74e",
    "3ca78c5633a86e7b7dc9db5259569f159dc27211e0f71fbe6dd185f325cd1d2d",
    "85de8a78d7ca467c90fa19881c5aba892d4d0ecbc05e458045b2fedf7f7317ed",
    "8f3c0aab3718b611dd021d6bf8d97040a896945b044e1efff993b456ecd7a986",
}

STRUCTURAL = re.compile(
    r"\b192\.168\.\d{1,3}\.\d{1,3}\b"        # home LAN addresses
    r"|\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b"   # tailnet (CGNAT)
    r"|/home/(?!user\b|alex\b|you\b)[a-z][a-z0-9_-]+/"                # real home dirs
    r"|/mnt/(?:data|nvme)/[a-z][a-z0-9_-]+/",                          # real data mounts
    re.I,
)
WORD = re.compile(r"[a-z][a-z0-9-]{2,}", re.I)

# test fixtures that intentionally contain LAN/home-like strings must opt out
# per line with this marker, so every exception is visible in review
ALLOW = "leak-scan: allow"

SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".icns", ".webp", ".onnx",
               ".pdf", ".zip", ".mp3", ".mp4", ".ttf", ".woff", ".woff2", ".jar"}
SKIP_PREFIX = ("installer/node_modules/", "installer/dist/", "scripts/dist/", "scripts/build/")


def h(word: str) -> str:
    return hashlib.sha256(word.lower().encode()).hexdigest()


def tracked() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True).stdout
    return [Path(p) for p in out.decode().split("\0") if p]


def messages(range_spec: str) -> list[str]:
    """Commit message blobs (subject + body) for every commit in a rev range.

    A docs-only commit can reintroduce a household name in its MESSAGE even
    when the file scrub is clean — scan the blobs too so the gate sees them.
    """
    out = subprocess.run(["git", "log", "--format=%B", range_spec],
                         capture_output=True, check=True).stdout
    return [line for line in out.decode(errors="replace").split("\n") if line.strip()]


def scan(lines, label: str = "") -> list[str]:
    hits = []
    for n, line in enumerate(line if isinstance(line, str) else str(line) for line in line_or_path_iter(lines)):
        if ALLOW in line:
            continue
        m = STRUCTURAL.search(line)
        if m:
            hits.append(f"{n}: private address/path {m.group(0)!r}")
            continue
        for w in WORD.findall(line):
            if h(w) in WORD_HASHES:
                hits.append(f"{n}: household name (hash {h(w)[:10]}…)")
                break
    return hits


def line_or_path_iter(paths):
    for p in paths:
        yield p


def scan_files(paths) -> list[str]:
    hits = []
    for p in paths:
        s = str(p)
        if p.suffix.lower() in SKIP_SUFFIX or s.startswith(SKIP_PREFIX) or not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits.extend(scan(text.splitlines()))
    return hits


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--hash"]:
        for w in args[1:]:
            print(f'    "{h(w)}",')
        sys.exit(0)
    if args[:1] == ["--messages"]:
        if not args[1:]:
            print("--messages needs a rev range like A..B", file=sys.stderr)
            sys.exit(2)
        hits = scan(messages(args[1]))
        if hits:
            print("LEAK SCAN FAILED — household-specific data in commit messages:")
            print("\n".join(hits[:200]))
            sys.exit(1)
        print(f"commit messages clean ({len(args[1])})")
        sys.exit(0)
    paths = [Path(a) for a in args] if args else tracked()
    hits = scan_files(paths)
    if hits:
        print("LEAK SCAN FAILED — household-specific data in the public repo:")
        print("\n".join(hits[:200]))
        sys.exit(1)
    print(f"leak scan clean ({len(paths)} files)")
