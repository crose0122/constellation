#!/usr/bin/env python3
"""Fail-closed privacy scan for a public Constellation candidate.

The default scans independent HEAD, index, tracked-worktree, and non-ignored
untracked states. Every blob is scanned as bytes, regardless of extension.
Household-specific deny terms are never stored here: CI may mount a private
newline-delimited file and set ``CONSTELLATION_PRIVATE_DENYLIST`` to its path.

    python3 tools/leak_scan.py
    python3 tools/leak_scan.py --require-private-denylist
    python3 tools/leak_scan.py --state worktree
    python3 tools/leak_scan.py --messages A..B   # commit messages in a range
    python3 tools/leak_scan.py --pushed REMOTE   # pre-push: stdin push lines
    python3 tools/leak_scan.py FILE [FILE ...]
"""

import codecs
import os
import re
import subprocess
import sys
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Iterable

GIT_TIMEOUT_SECONDS = 10
DENYLIST_ENV = "CONSTELLATION_PRIVATE_DENYLIST"
MAX_DENY_SEPARATOR_LENGTH = 8
MAX_DENY_TERM_CHARS = 256
MAX_DENY_TERMS = 1024
MAX_NORMALIZED_CHARS = 4 * 1024 * 1024
REQUIRE_DENYLIST_FLAG = "--require-private-denylist"

# Unicode Default_Ignorable_Code_Point, copied from DerivedCoreProperties.txt
# (unchanged from Unicode 15.0 through 16.0) and coalesced into inclusive
# ranges. Python's unicodedata does not expose this derived property, so the
# official ranges are hard-coded here and pinned by a regression test (plus an
# independent cross-check against Perl's UCD when available). These code
# points render as nothing, so anywhere in scanned text they are treated as
# "deleted or a word gap" (see _canonical_scan_text); never as significant.
DEFAULT_IGNORABLE_RANGES = (
    (0x00AD, 0x00AD),    # soft hyphen
    (0x034F, 0x034F),    # combining grapheme joiner
    (0x061C, 0x061C),    # Arabic letter mark
    (0x115F, 0x1160),    # Hangul choseong/jungseong fillers
    (0x17B4, 0x17B5),    # Khmer inherent vowels
    (0x180B, 0x180F),    # Mongolian free variation selectors, vowel separator
    (0x200B, 0x200F),    # ZWSP, ZWNJ, ZWJ, LRM, RLM
    (0x202A, 0x202E),    # bidi embeddings and overrides
    (0x2060, 0x206F),    # word joiner, invisible operators, bidi isolates, deprecated
    (0x3164, 0x3164),    # Hangul filler
    (0xFE00, 0xFE0F),    # variation selectors 1-16
    (0xFEFF, 0xFEFF),    # zero width no-break space / BOM
    (0xFFA0, 0xFFA0),    # halfwidth Hangul filler
    (0xFFF0, 0xFFF8),    # unassigned, reserved default-ignorable
    (0x1BCA0, 0x1BCA3),  # shorthand format controls
    (0x1D173, 0x1D17A),  # musical symbol format controls
    (0xE0000, 0xE0FFF),  # tags, variation selectors supplement, reserved
)
DEFAULT_IGNORABLE_CHARACTERS = frozenset(
    chr(codepoint)
    for first, last in DEFAULT_IGNORABLE_RANGES
    for codepoint in range(first, last + 1)
)

# Unicode's Bidi_Control property and deprecated bidi formatting controls,
# plus format controls whose documented rendering is zero-width. All are
# Default_Ignorable; the explicit lists keep the audited inventory visible.
# Other Cf characters (for example Arabic number signs and interlinear
# annotations) remain significant rather than becoming invented boundaries.
BIDI_FORMAT_SEPARATORS = frozenset(
    "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e"
    "\u2066\u2067\u2068\u2069\u206a\u206b\u206c\u206d\u206e\u206f"
)
ZERO_WIDTH_FORMAT_SEPARATORS = frozenset(
    "\u00ad\u180e\u200b\u200c\u200d\u2060\u2061\u2062\u2063\u2064\ufeff"
)
INVISIBLE_CHARACTERS = (
    DEFAULT_IGNORABLE_CHARACTERS | BIDI_FORMAT_SEPARATORS | ZERO_WIDTH_FORMAT_SEPARATORS
)
# Visible-width blanks that are not Unicode whitespace. The Hangul fillers are
# also Default_Ignorable (so they may vanish in-word); braille blank is not.
BLANK_FILLER_SEPARATORS = frozenset("\u115f\u1160\u2800\u3164\uffa0")
# Unicode punctuation/math forms commonly confusable with path slash or dot
# separators. Compatibility forms are listed too, even where NFKC reduces
# them before this policy runs, so the intended coverage stays auditable.
SLASH_DOT_LOOKALIKE_SEPARATORS = frozenset(
    "\u2044\u2215\u29f8\uff0f\u2024\u2027\u2219\u22c5\u3002\ufe52\uff0e\uff61"
)
NORMALIZED_SEPARATOR_CHARACTERS = (
    BIDI_FORMAT_SEPARATORS
    | ZERO_WIDTH_FORMAT_SEPARATORS
    | BLANK_FILLER_SEPARATORS
    | SLASH_DOT_LOOKALIKE_SEPARATORS
)
# Internal marker for "an invisible run was here": matched either as nothing
# (in-word deletion) or as one word gap. NUL cannot survive into canonical
# text any other way (it is folded to a line break first) and deny terms
# reject it, so the marker is unambiguous.
_INVISIBLE_MARK = "\0"

class GitScanError(RuntimeError):
    """A repository state could not be enumerated or read safely."""


class DenylistError(RuntimeError):
    """A configured external denylist could not be read safely."""


# Public structural rules contain no household-specific values. Canonical
# localhost is intentionally allowed because product and test servers bind it;
# other IPv4 loopback endpoints are not valid public examples.
STRUCTURAL = re.compile(
    r"\b(?:"
    r"10(?:\.\d{1,3}){3}"
    r"|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}"
    r"|192\.168(?:\.\d{1,3}){2}"
    r"|169\.254(?:\.\d{1,3}){2}"
    r"|127\.(?!0\.0\.1\b)\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])(?:\.\d{1,3}){2}"
    r")\b"
    r"|/home/(?!user\b|example\b|you\b)[a-z][a-z0-9_-]+/"
    r"|/mnt/(?:data|nvme)(?![a-z0-9_.-])"
    r"|\bmnt-(?:data|nvme)\.mount\b"
    r"|(?<![0-9a-f:])(?:f[cd][0-9a-f]{2}|fe[89ab][0-9a-f])"
    r"(?::[0-9a-f]{0,4}){1,7}(?![0-9a-f:])",
    re.I,
)

ROLE_NARRATION = re.compile(
    r"\bthe\s+(?:(?:project|build)\s+)?"
    r"(?:founder|co-founder|reviewer|agent|patron|review\s+lead)\b"
    r"|\b(?:AMS|OPS)(?:\s+task)?\s*#\d+\b",
    re.I,
)
ATTRIBUTED_QUOTE = re.compile(
    r"\b(?:founder|co-founder|reviewer|agent|maintainer|patron)\b"
    r"\s+(?:said|wrote|asked|decided)\s*:\s*[\"“][^\"”]+[\"”]"
    r"|[\"“][^\"”]+[\"”]\s*(?:[-—]|\(|,)\s*"
    r"(?:20\d{2}-\d{2}-\d{2}\s*,?\s*)?"
    r"(?:founder|co-founder|reviewer|agent|maintainer|patron)\b",
    re.I,
)
CONCRETE_TOPOLOGY = re.compile(
    r"\bthe\s+[a-z0-9-]+\s+(?:host|server|vm|node)\b"
    r".{0,240}\b(?:runs?|hosts?|holds?|shares?|co-?located|storage|inference)\b"
    r".{0,240}(?:\bport\s+\d{2,5}\b|:\d{2,5}\b|\b(?:storage|inference)\b)",
    re.I | re.S,
)
PRIVATE_RELATIONSHIP = re.compile(
    r"\b(?:founder|co-founder|reviewer|agent|maintainer|patron)'s\s+"
    r"(?:cousin|sibling|parent|child|relative)(?:'s)?\s+"
    r"(?:family|household)\b",
    re.I,
)
BROKEN_PLACEHOLDER = re.compile(
    r"https?://the\s+[a-z0-9-]+\s+(?:host|server|vm|node)\b"
    r"|<\s*(?:lan[-_ ]?ip|host|server)(?!\s*>)",
    re.I,
)
LIVE_HOUSEHOLD_STATE = re.compile(
    r"\b(?:production|current|live|real)\s+"
    r"(?:library|inventory|install|deployment)\b"
    r".{0,240}\b(?:\d[\d,._~]*\s+photos?|vault\s+(?:exists|open|closed|mounted))\b"
    r"|\b\d[\d,._~]*\s+photos?\s+(?:ingested|indexed|in\s+the\s+pipeline)\b"
    r"|\b(?:screened|ingested|indexed)\s+\d[\d,._~]*\s+photos?"
    r".{0,160}\bvaulted\s+\d"
    r"|\bdatabase\b.{0,160}\b\d[\d,._~]*\s+rows\b"
    r".{0,160}\b\d[\d,._~]*\s+files\b"
    r"|\bOOM-killed\b.{0,160}\b20\d{2}-\d{2}-\d{2}\b"
    r"|\bproduction\s+run\b.{0,240}\b\d[\d,._~]*\s+"
    r"(?:photos?|files?|rows?|hits?)\b.{0,240}\b20\d{2}-\d{2}-\d{2}\b"
    r"|\b\d[\d,._~]*\s+hits?\s+in\s+\d[\d,._~]*\s+photos?\b"
    r".{0,160}\breal\s+detection\s+rate\b",
    re.I | re.S,
)
SEMANTIC_RULES = (
    (ATTRIBUTED_QUOTE, "attributed quoted speech"),
    (CONCRETE_TOPOLOGY, "concrete topology disclosure"),
    (PRIVATE_RELATIONSHIP, "private relationship disclosure"),
    (BROKEN_PLACEHOLDER, "broken redaction placeholder"),
    (LIVE_HOUSEHOLD_STATE, "live household state disclosure"),
    (ROLE_NARRATION, "internal role narration"),
)


def load_deny_terms(path: Path | str) -> tuple[str, ...]:
    """Load private terms from a mounted file, failing closed on any problem."""
    candidate = Path(path)
    try:
        text = candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise DenylistError(
            f"configured denylist is unreadable ({exc.__class__.__name__})"
        ) from exc
    terms = []
    for raw in text.splitlines():
        term = raw.strip()
        if not term or term.startswith("#"):
            continue
        if "\0" in term:
            raise DenylistError("configured denylist contains a NUL byte")
        if "\ufeff" in term:
            raise DenylistError("configured denylist contains a byte-order mark")
        if not any(character.isalnum() for character in term):
            raise DenylistError("configured denylist contains a malformed term")
        if len(term) > MAX_DENY_TERM_CHARS:
            raise DenylistError("configured denylist contains an oversized term")
        terms.append(term)
        if len(terms) > MAX_DENY_TERMS:
            raise DenylistError("configured denylist contains too many terms")
    unique_terms = tuple(dict.fromkeys(terms))
    if not unique_terms:
        raise DenylistError("configured denylist contains no usable terms")
    return unique_terms


def _codepoint_class(codepoints) -> str:
    """Compile sorted code points into a compact regex character class."""
    parts = []
    run_start = previous = None
    for codepoint in sorted(codepoints):
        if previous is not None and codepoint == previous + 1:
            previous = codepoint
            continue
        if run_start is not None:
            parts.append((run_start, previous))
        run_start = previous = codepoint
    if run_start is not None:
        parts.append((run_start, previous))
    return "[" + "".join(
        re.escape(chr(first)) if first == last
        else f"{re.escape(chr(first))}-{re.escape(chr(last))}"
        for first, last in parts
    ) + "]"


@lru_cache(maxsize=1)
def _canonical_tables():
    """Build translation tables once per process (C-speed per-char mapping)."""
    invisible = {ord(character): _INVISIBLE_MARK for character in INVISIBLE_CHARACTERS}
    invisible[0] = "\n"
    separators = dict(invisible)
    # The second pass must keep the markers produced by the first pass.
    del separators[0]
    for codepoint in range(0x10000):  # every Z/whitespace code point is in the BMP
        character = chr(codepoint)
        if character != "\n" and (
            character.isspace() or unicodedata.category(character).startswith("Z")
        ):
            separators[codepoint] = " "
    for character in "_-/\\." + "".join(
        BLANK_FILLER_SEPARATORS | SLASH_DOT_LOOKALIKE_SEPARATORS
    ):
        separators[ord(character)] = " "
    # An invisible run directly before anything that NFKC turns into a
    # combining mark (or a Hangul medial/final jamo) is deleted before NFKC so
    # composition still happens. The set is derived from each code point's
    # NFKC form, not its raw category: halfwidth kana voiced marks (U+FF9E,
    # U+FF9F), Hangul compatibility jamo, and Thai/Lao SARA AM are not
    # category M themselves but fold to a combining start.
    hangul_joining_jamo = set(range(0x1161, 0x1200))    # medial/final jamo
    hangul_joining_jamo.update(range(0xD7B0, 0xD800))   # jamo extended-B

    def _joins_previous(character: str) -> bool:
        return (
            unicodedata.category(character[0]).startswith("M")
            or ord(character[0]) in hangul_joining_jamo
        )

    # Unassigned, private-use, and surrogate code points are NFKC-stable and
    # not marks, so skipping them keeps this one-time table build cheap.
    joining = {
        codepoint for codepoint in range(0x110000)
        if unicodedata.category(chr(codepoint)) not in ("Cn", "Co", "Cs")
        and chr(codepoint) not in INVISIBLE_CHARACTERS
        and (
            _joins_previous(chr(codepoint))
            or _joins_previous(unicodedata.normalize("NFKC", chr(codepoint)) or "\0")
        )
    }
    # Keep whole jamo blocks (including currently unassigned slots).
    joining |= hangul_joining_jamo
    before_joining = re.compile(r"\0+(?=" + _codepoint_class(joining) + ")")
    # Cheap prefilter: fold every joining code point to one sentinel so the
    # (comparatively slow) lookahead substitution only runs when needed.
    joining_probe = dict.fromkeys(joining, "\x01")
    return invisible, separators, before_joining, joining_probe


# One pass: drop markers touching a visible separator (the gap already
# exists) and collapse any remaining marker run to a single marker.
_REDUNDANT_MARKS = re.compile(r"(?<=[ \n\0])\0+|\0+(?=[ \n])")


def _canonical_scan_text(text: str) -> str:
    """NFKC/casefold text and canonicalize invisible and separator characters.

    NFKC closes composed/decomposed and compatibility-form bypasses. It is not
    a general visual-confusable mapping: cross-script homoglyphs (for example
    Cyrillic "а" for Latin "a") and HTML/URL-encoded forms are out of scope.
    Default_Ignorable code points and bidi/format controls render as nothing,
    so each run of them becomes a single marker that deny patterns match both
    as deleted (inside a word) and as one word gap (between words). Slashes,
    dots, ASCII path separators, Unicode whitespace/Z separators, blank
    fillers, and slash/dot lookalikes are bounded visible separators; other
    punctuation and format controls remain significant.
    """
    invisible, separators, before_joining, joining_probe = _canonical_tables()
    marked = text.translate(invisible)
    if _INVISIBLE_MARK in marked and "\0\x01" in marked.translate(joining_probe):
        marked = before_joining.sub("", marked)
    normalized = unicodedata.normalize("NFKC", marked).casefold().translate(separators)
    if _INVISIBLE_MARK in normalized:
        normalized = _REDUNDANT_MARKS.sub("", normalized)
    return normalized


def _term_pieces(term: str) -> tuple[str, ...]:
    """Canonical deny-term words; invisible characters inside a term vanish."""
    canonical = _canonical_scan_text(term).replace(_INVISIBLE_MARK, "")
    return tuple(piece for piece in re.split(r"[ \n]+", canonical) if piece)


_TERM_END = object()


def _trie_regex(node, in_word: bool, marks: bool) -> str:
    """Emit a prefix-factored regex so shared term prefixes are tried once."""
    branches = []
    for key, child in node.items():
        if key is _TERM_END:
            continue
        if key == " ":
            # One word gap: a bounded run of visible separators, or a single
            # collapsed invisible run.
            atom = rf"(?:[ \n]{{1,{MAX_DENY_SEPARATOR_LENGTH}}}|\0)"
            branches.append(atom + _trie_regex(child, False, marks))
        else:
            atom = (r"\0?" if in_word and marks else "") + re.escape(key)
            branches.append(atom + _trie_regex(child, True, marks))
    optional = _TERM_END in node
    if not branches:
        return ""
    if len(branches) == 1 and not optional:
        return branches[0]
    return "(?:" + "|".join(branches) + ")" + ("?" if optional else "")


@lru_cache(maxsize=8)
def _deny_pattern(terms: tuple[str, ...], marks: bool = True):
    """Compile one bounded, prefix-factored pattern instead of N scans.

    ``marks`` permits an invisible-run marker between any two characters of
    a word. Text without markers never needs that (much larger) pattern, so
    callers request it only when the canonical text contains a marker.
    """
    if len(terms) > MAX_DENY_TERMS:
        raise DenylistError("configured denylist contains too many terms")
    trie: dict = {}
    for term in terms:
        if len(term) > MAX_DENY_TERM_CHARS:
            raise DenylistError("configured denylist contains an oversized term")
        pieces = _term_pieces(term)
        if not pieces:
            continue
        node = trie
        for character in " ".join(pieces):
            node = node.setdefault(character, {})
        node[_TERM_END] = True
    if not trie:
        return None
    # Python's \w is Unicode-aware and includes underscore. Subtracting
    # underscore leaves the Unicode/ASCII alphanumeric token boundary we need.
    # The invisible marker is non-word, so it also counts as a boundary.
    return re.compile(rf"(?<![^\W_])(?:{_trie_regex(trie, False, marks)})(?![^\W_])")


def _configured_term_match(text: str, deny_terms: Iterable[str]):
    terms = tuple(deny_terms)
    if not terms:
        return None, False
    if len(text) > MAX_NORMALIZED_CHARS:
        return None, True
    normalized = _canonical_scan_text(text)
    if len(normalized) > MAX_NORMALIZED_CHARS:
        return None, True
    pattern = _deny_pattern(terms, _INVISIBLE_MARK in normalized)
    match = pattern.search(normalized) if pattern else None
    if match:
        return (normalized, match), False
    return None, False


def _display_path(path: bytes | str) -> str:
    if isinstance(path, bytes):
        path = os.fsdecode(path)
    return path.encode("unicode_escape", errors="backslashreplace").decode("ascii")


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def scan_bytes(label: str, data: bytes, deny_terms: Iterable[str] = ()) -> list[str]:
    """Scan a blob; NUL and line boundaries cannot split semantic matches."""
    hits: list[str] = []
    text = data.decode("utf-8", errors="surrogateescape").replace("\0", "\n")

    configured_match, oversized = _configured_term_match(text, deny_terms)
    if oversized:
        return [f"{label}: normalized scan limit exceeded"]

    # Keep STRUCTURAL as a named ratchet: disabling it must break the regression
    # suite. Structural checks report each affected line.
    for line_number, line in enumerate(text.splitlines(), 1):
        match = STRUCTURAL.search(line)
        if match:
            hits.append(f"{label}:{line_number}: private address/path")

    # Configured terms operate on the complete blob so line and NUL boundaries
    # cannot evade a multi-token term. Findings never reveal the configured term.
    if configured_match:
        normalized, match = configured_match
        hits.append(
            f"{label}:{_line_number(normalized, match.start())}: configured private term"
        )

    # Semantic rules deliberately operate on the complete decoded blob. Their
    # bounded wildcards cross line and NUL separators but not arbitrary files.
    for pattern, description in SEMANTIC_RULES:
        match = pattern.search(text)
        if match:
            hits.append(f"{label}:{_line_number(text, match.start())}: {description}")

    return hits


def scan(paths, deny_terms: Iterable[str] = ()) -> list[str]:
    hits: list[str] = []
    terms = tuple(deny_terms)
    for path in paths:
        path = Path(path)
        filename_match, filename_oversized = _configured_term_match(
            os.fspath(path), terms
        )
        label = (
            "requested:[filename redacted]"
            if filename_match or filename_oversized
            else _display_path(os.fspath(path))
        )
        if filename_oversized:
            hits.append(f"{label}: normalized filename scan limit exceeded")
        elif filename_match:
            hits.append(f"{label}: configured private term in filename")
        if not path.exists() and not path.is_symlink():
            hits.append(f"{label}: missing requested path")
            continue
        if not path.is_file():
            hits.append(f"{label}: requested path is not a regular file")
            continue
        try:
            hits.extend(scan_bytes(label, path.read_bytes(), terms))
        except OSError as exc:
            hits.append(f"{label}: unreadable tracked file ({exc.__class__.__name__})")
    return hits


def _git_environment() -> dict[str, str]:
    """Return an environment that cannot redirect Git away from ``cwd``."""
    return {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        env=_git_environment(),
        capture_output=True,
        check=True,
        timeout=GIT_TIMEOUT_SECONDS,
    )


def _git(root: Path, *args: str) -> bytes:
    try:
        return _run_git(root, *args).stdout
    except subprocess.TimeoutExpired as exc:
        raise GitScanError("git command timed out") from exc
    except subprocess.CalledProcessError as exc:
        raise GitScanError(f"git command failed (exit {exc.returncode})") from exc
    except OSError as exc:
        raise GitScanError(f"git command unavailable ({exc.__class__.__name__})") from exc


def _git_blob(root: Path, object_id: bytes) -> bytes:
    # Stored bytes only: a local refs/replace entry must not swap in clean
    # content for a blob that is actually committed (and pushed).
    return _git(root, "--no-replace-objects", "cat-file", "blob", object_id.decode("ascii"))


def scan_git_state(
    state: str,
    root: Path | str = ".",
    deny_terms: Iterable[str] = (),
) -> tuple[list[str], int]:
    start = Path(root)
    try:
        root = Path(_git(start, "rev-parse", "--show-toplevel").decode(
            "utf-8", "surrogateescape"
        ).strip())
    except (GitScanError, subprocess.SubprocessError, OSError) as exc:
        detail = "git command timed out" if isinstance(exc, subprocess.TimeoutExpired) else str(exc)
        return [f"{state}: git discovery failed ({detail})"], 0

    entries: list[tuple[bytes, bytes | None]] = []
    state_hits: list[str] = []
    try:
        if state == "head":
            for record in _git(root, "ls-tree", "-rz", "HEAD").split(b"\0"):
                if not record:
                    continue
                metadata, path = record.split(b"\t", 1)
                _mode, kind, object_id = metadata.split(b" ", 2)
                if kind == b"blob":
                    entries.append((path, object_id))
        elif state == "index":
            for record in _git(root, "ls-files", "-z", "--stage").split(b"\0"):
                if not record:
                    continue
                metadata, path = record.split(b"\t", 1)
                _mode, object_id, stage = metadata.split(b" ", 2)
                if stage == b"0":
                    entries.append((path, object_id))
                else:
                    # Unmerged stages are still index entries: scan both their
                    # names and blobs. The diagnostic never prints the name.
                    entries.append((path, object_id))
                    state_hits.append(
                        f"index: unmerged index entry (stage {stage.decode()})"
                    )
        elif state == "worktree":
            for path in _git(root, "ls-files", "-z", "--cached").split(b"\0"):
                if path:
                    entries.append((path, None))
        elif state == "untracked":
            for path in _git(
                root, "ls-files", "-z", "--others", "--exclude-standard"
            ).split(b"\0"):
                if path:
                    entries.append((path, None))
        else:
            raise ValueError(f"unknown git state: {state}")
    except (GitScanError, subprocess.SubprocessError, OSError, ValueError) as exc:
        return [f"{state}: git discovery failed ({exc})"], 0

    hits = list(state_hits)
    scanned = 0
    terms = tuple(deny_terms)
    for entry_number, (path, object_id) in enumerate(entries, 1):
        filename_match, filename_oversized = _configured_term_match(
            os.fsdecode(path), terms
        )
        label = (
            f"{state}:entry-{entry_number}:[filename redacted]"
            if filename_match or filename_oversized
            else f"{state}:{_display_path(path)}"
        )
        if filename_oversized:
            hits.append(f"{label}: normalized filename scan limit exceeded")
        elif filename_match:
            hits.append(f"{label}: configured private term in filename")
        if state in {"worktree", "untracked"}:
            candidate = root / os.fsdecode(path)
            if candidate.is_symlink():
                try:
                    data = os.fsencode(os.readlink(candidate))
                except OSError as exc:
                    hits.append(
                        f"{label}: unreadable tracked symlink ({exc.__class__.__name__})"
                    )
                    scanned += 1
                    continue
            elif not candidate.is_file():
                continue
            else:
                try:
                    data = candidate.read_bytes()
                except OSError as exc:
                    hits.append(
                        f"{label}: unreadable tracked file ({exc.__class__.__name__})"
                    )
                    scanned += 1
                    continue
        else:
            try:
                data = _git_blob(root, object_id or b"")
            except (GitScanError, subprocess.SubprocessError, OSError) as exc:
                hits.append(f"{label}: blob read failed ({exc})")
                scanned += 1
                continue
        hits.extend(scan_bytes(label, data, terms))
        scanned += 1
    return hits, scanned


_HEX_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


def _raw_git(root: Path, *args: str) -> bytes:
    """Read objects as stored: replace refs must not substitute content."""
    return _git(root, "--no-replace-objects", *args)


def _commit_message(root: Path, commit: str, label: str) -> tuple[bytes | None, str | None]:
    """Return the stored message bytes (decoded to UTF-8 when declared)."""
    raw = _raw_git(root, "cat-file", "commit", commit)
    headers, separator, message = raw.partition(b"\n\n")
    if not separator:
        return b"", None
    declared = [header[len(b"encoding "):].decode("ascii", "replace").strip()
                for header in headers.split(b"\n") if header.startswith(b"encoding ")]
    if len(declared) > 1:
        return None, f"{label}: undecodable commit message"
    encoding = declared[0] if declared else None
    if not encoding or encoding.lower().replace("_", "-") in {"utf-8", "utf8"}:
        return message, None
    try:
        codecs.lookup(encoding)
        return message.decode(encoding).encode("utf-8", "surrogateescape"), None
    except (LookupError, UnicodeError, ValueError):
        return None, f"{label}: undecodable commit message"


def _scan_commit_messages(root: Path, commits, terms) -> tuple[list[str], int]:
    hits: list[str] = []
    count = 0
    for commit in commits:
        label = f"message:{commit[:12]}"
        try:
            message, problem = _commit_message(root, commit, label)
        except (GitScanError, subprocess.SubprocessError, OSError) as exc:
            hits.append(f"{label}: commit read failed ({exc})")
            count += 1
            continue
        if problem:
            hits.append(problem)
        else:
            hits.extend(scan_bytes(label, message, terms))
        count += 1
    return hits, count


def scan_messages(
    range_spec: str,
    root: Path | str = ".",
    deny_terms: Iterable[str] = (),
) -> tuple[list[str], int]:
    """Scan every commit message (subject + body) in a revision range.

    A commit can reintroduce private text in its MESSAGE while the tree stays
    clean, and a pushed message is as public as a blob. Messages are read
    from the stored commit objects (not ``git log`` output, which stops at a
    NUL, follows replace refs, and re-encodes), then get the same byte-level
    rules as a file. Findings are labelled by commit id and never print the
    matched text.
    """
    if not range_spec or range_spec.startswith("-"):
        return ["messages: invalid revision range"], 0
    try:
        commits = _raw_git(
            Path(root), "rev-list", "--end-of-options", range_spec, "--",
        ).decode("ascii").split()
    except (GitScanError, subprocess.SubprocessError, OSError, UnicodeError) as exc:
        return [f"messages: git discovery failed ({exc})"], 0
    return _scan_commit_messages(Path(root), commits, tuple(deny_terms))


def _remote_heads(root: Path, remote: str) -> list[str] | None:
    """Branch and tag tips the remote itself reports (``git ls-remote``).

    Local ``refs/remotes`` are not trusted: they can be stale (a branch
    withdrawn from the remote) or match another remote by prefix. Returns
    None when the remote cannot be asked, and callers then exclude nothing.
    """
    try:
        listing = _git(root, "ls-remote", "--heads", "--tags", "--refs",
                       "--end-of-options", remote)
    except GitScanError:
        return None
    heads = []
    for line in listing.decode("utf-8", "surrogateescape").splitlines():
        object_id = line.split("\t", 1)[0]
        if _HEX_OBJECT_ID.fullmatch(object_id):
            heads.append(object_id)
    return heads


def _existing_commits(root: Path, object_ids: Iterable[str]) -> list[str]:
    present = []
    for object_id in object_ids:
        if not object_id or set(object_id) == {"0"} or not _HEX_OBJECT_ID.fullmatch(object_id):
            continue
        try:
            _raw_git(root, "cat-file", "-e", f"{object_id}^{{commit}}")
        except GitScanError:
            continue
        present.append(object_id)
    return present


def _tag_messages(root: Path, object_id: str, label: str, terms) -> tuple[list[str], int]:
    """Scan an annotated tag's message (and any tag it points at)."""
    hits: list[str] = []
    count = 0
    for _depth in range(16):
        try:
            kind = _raw_git(root, "cat-file", "-t", object_id).decode("ascii").strip()
        except (GitScanError, UnicodeError) as exc:
            return hits + [f"{label}: object read failed ({exc})"], count
        if kind != "tag":
            if kind != "commit":
                hits.append(f"{label}: unsupported non-commit push target")
            return hits, count
        raw = _raw_git(root, "cat-file", "tag", object_id)
        headers, _separator, message = raw.partition(b"\n\n")
        hits.extend(scan_bytes(f"{label}:tag-message", message, terms))
        count += 1
        target = next((h[len(b"object "):].decode("ascii", "replace")
                       for h in headers.split(b"\n") if h.startswith(b"object ")), "")
        if not _HEX_OBJECT_ID.fullmatch(target):
            return hits + [f"{label}: malformed tag object"], count
        object_id = target
    return hits + [f"{label}: tag chain too deep"], count


def _changed_blobs(root: Path, commit: str) -> list[tuple[bytes, str]]:
    """(path, blob id) for every blob a commit introduces relative to its parents."""
    output = _raw_git(root, "diff-tree", "-r", "-z", "--root", "-m", "--no-commit-id",
                      "--no-renames", "--no-ext-diff", commit)
    fields = output.split(b"\0")
    changes = []
    index = 0
    while index < len(fields):
        metadata = fields[index]
        if not metadata.startswith(b":"):
            index += 1
            continue
        path = fields[index + 1] if index + 1 < len(fields) else b""
        index += 2
        parts = metadata[1:].split(b" ")
        if len(parts) < 5:
            raise GitScanError("unparseable diff-tree record")
        new_mode, new_id = parts[1], parts[3].decode("ascii")
        if set(new_id) == {"0"} or new_mode == b"160000":
            continue  # deletion or submodule pointer: no new blob bytes
        changes.append((path, new_id))
    return changes


def scan_pushed(
    lines: Iterable[str],
    root: Path | str = ".",
    remote: str = "origin",
    deny_terms: Iterable[str] = (),
    published: Iterable[str] | None = None,
) -> tuple[list[str], int]:
    """Scan what a push publishes, from pre-push style lines.

    Each line is ``<local ref> <local sha> <remote ref> <remote sha>``. For
    every non-deletion the published ref name, any annotated tag message, and
    every commit not already published are scanned: its stored message and
    every blob (content and path) it introduces, so a leak added and removed
    within the pushed range is still caught. The checked-out branch and
    worktree are irrelevant.

    "Already published" is the ref's previous remote value plus either the
    tips the remote reports (pre-push; ``published`` is None) or exactly the
    ``published`` commits given by the caller (CI, where every branch was
    fetched after the push and so cannot be trusted as prior state).
    """
    root = Path(root)
    terms = tuple(deny_terms)
    if published is None and (not remote or remote.startswith("-")):
        return ["pushed: invalid remote"], 0
    hits: list[str] = []
    commits: list[str] = []
    seen_commits: set[str] = set()
    trusted: list[str] | None = None
    tag_count = 0
    for line_number, line in enumerate(lines, 1):
        line = line.rstrip("\r\n")
        if not line.strip():
            continue
        fields = line.split(" ")
        if len(fields) != 4 or not _HEX_OBJECT_ID.fullmatch(fields[1]) \
                or not _HEX_OBJECT_ID.fullmatch(fields[3]):
            hits.append(f"pushed:line-{line_number}: malformed push line")
            continue
        _local_ref, local_sha, remote_ref, remote_sha = fields
        if set(local_sha) == {"0"}:
            continue  # deleting a remote ref publishes nothing
        hits.extend(scan_bytes(f"pushed:line-{line_number}:[ref name]",
                               remote_ref.encode("utf-8", "surrogateescape"), terms))
        tag_hits, tags = _tag_messages(root, local_sha, f"pushed:line-{line_number}", terms)
        hits.extend(tag_hits)
        tag_count += tags
        if trusted is None:
            if published is not None:
                trusted = _existing_commits(root, published)
            else:
                trusted = _existing_commits(root, _remote_heads(root, remote) or [])
        exclusions = _existing_commits(root, [remote_sha]) + trusted
        try:
            listed = _raw_git(root, "rev-list", local_sha, "--not", *exclusions, "--") \
                if exclusions else _raw_git(root, "rev-list", local_sha, "--")
        except GitScanError as exc:
            hits.append(f"pushed:line-{line_number}: git discovery failed ({exc})")
            continue
        for commit in listed.decode("ascii").split():
            if commit not in seen_commits:
                seen_commits.add(commit)
                commits.append(commit)

    message_hits, count = _scan_commit_messages(root, commits, terms)
    hits.extend(message_hits)
    scanned_blobs: set[str] = set()
    scanned_names: dict[bytes, tuple[str | None, bool]] = {}
    for commit in commits:
        try:
            changes = _changed_blobs(root, commit)
        except (GitScanError, subprocess.SubprocessError, OSError, UnicodeError) as exc:
            hits.append(f"pushed:{commit[:12]}: tree read failed ({exc})")
            continue
        for path, blob_id in changes:
            if path not in scanned_names:
                scanned_names[path] = _configured_term_match(os.fsdecode(path), terms)
            filename_match, filename_oversized = scanned_names[path]
            label = (
                f"pushed:{commit[:12]}:[filename redacted]"
                if filename_match or filename_oversized
                else f"pushed:{commit[:12]}:{_display_path(path)}"
            )
            if filename_oversized:
                hits.append(f"{label}: normalized filename scan limit exceeded")
            elif filename_match:
                hits.append(f"{label}: configured private term in filename")
            if blob_id in scanned_blobs:
                continue
            scanned_blobs.add(blob_id)
            try:
                data = _git_blob(root, blob_id.encode("ascii"))
            except (GitScanError, subprocess.SubprocessError, OSError) as exc:
                hits.append(f"{label}: blob read failed ({exc})")
                continue
            hits.extend(scan_bytes(label, data, terms))
    return hits, count + tag_count + len(scanned_blobs)


def _configured_terms(require_private_denylist: bool = False) -> tuple[str, ...]:
    configured = os.environ.get(DENYLIST_ENV)
    if require_private_denylist and not configured:
        raise DenylistError("required private denylist is not configured")
    return load_deny_terms(configured) if configured else ()


def main(args: list[str]) -> int:
    require_private_denylist = REQUIRE_DENYLIST_FLAG in args
    args = [arg for arg in args if arg != REQUIRE_DENYLIST_FLAG]
    try:
        deny_terms = _configured_terms(require_private_denylist)
    except DenylistError as exc:
        print(f"LEAK SCAN FAILED — {exc}", file=sys.stderr)
        return 1

    if args[:1] == ["--pushed"]:
        if len(args) != 2 or not args[1] or args[1].startswith("-"):
            print("usage: tools/leak_scan.py --pushed REMOTE < pre-push-stdin",
                  file=sys.stderr)
            return 2
        lines = sys.stdin.buffer.read().decode("utf-8", "surrogateescape").splitlines()
        hits, count = scan_pushed(lines, remote=args[1], deny_terms=deny_terms)
        if hits:
            print("LEAK SCAN FAILED — private data in pushed commits or commit messages:")
            print("\n".join(hits[:200]))
            if len(hits) > 200:
                print(f"... {len(hits) - 200} more finding(s)")
            return 1
        print(f"pushed commits clean ({count} commit messages and new blobs)")
        return 0

    if args[:1] == ["--pushed-ci"]:
        # CI: stdin carries the pushed ref line; the argument is the one
        # commit trusted as already published (default-branch tip), or "".
        if len(args) != 2 or (args[1] and not _HEX_OBJECT_ID.fullmatch(args[1])):
            print("usage: tools/leak_scan.py --pushed-ci BASE_SHA_OR_EMPTY < push-line",
                  file=sys.stderr)
            return 2
        lines = sys.stdin.buffer.read().decode("utf-8", "surrogateescape").splitlines()
        if not any(line.strip() for line in lines):
            print("LEAK SCAN FAILED — no pushed ref line on stdin", file=sys.stderr)
            return 1
        hits, count = scan_pushed(lines, published=[args[1]] if args[1] else [],
                                  deny_terms=deny_terms)
        if hits:
            print("LEAK SCAN FAILED — private data in pushed commits or commit messages:")
            print("\n".join(hits[:200]))
            if len(hits) > 200:
                print(f"... {len(hits) - 200} more finding(s)")
            return 1
        print(f"pushed commits clean ({count} commit messages and new blobs)")
        return 0

    if args[:1] == ["--messages"]:
        if len(args) != 2 or not args[1] or args[1].startswith("-"):
            print("usage: tools/leak_scan.py --messages A..B", file=sys.stderr)
            return 2
        hits, count = scan_messages(args[1], deny_terms=deny_terms)
        if hits:
            print("LEAK SCAN FAILED — private data in commit messages:")
            print("\n".join(hits[:200]))
            if len(hits) > 200:
                print(f"... {len(hits) - 200} more finding(s)")
            return 1
        print(f"commit messages clean ({count} commit messages in range)")
        return 0

    if args[:1] == ["--state"]:
        if len(args) != 2 or args[1] not in {
            "head", "index", "worktree", "untracked", "all"
        }:
            print(
                "usage: tools/leak_scan.py --state "
                "{head,index,worktree,untracked,all}",
                file=sys.stderr,
            )
            return 2
        states = (
            ("head", "index", "worktree", "untracked")
            if args[1] == "all" else (args[1],)
        )
        hits: list[str] = []
        count = 0
        for state in states:
            state_hits, state_count = scan_git_state(
                state, deny_terms=deny_terms
            )
            hits.extend(state_hits)
            count += state_count
    elif args:
        paths = [Path(arg) for arg in args]
        hits = scan(paths, deny_terms=deny_terms)
        count = len(paths)
    else:
        hits = []
        count = 0
        for state in ("head", "index", "worktree", "untracked"):
            state_hits, state_count = scan_git_state(
                state, deny_terms=deny_terms
            )
            hits.extend(state_hits)
            count += state_count

    if hits:
        print("LEAK SCAN FAILED — private data in the public repository candidate:")
        print("\n".join(hits[:200]))
        if len(hits) > 200:
            print(f"... {len(hits) - 200} more finding(s)")
        return 1
    print(f"leak scan clean ({count} blobs across requested states)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
