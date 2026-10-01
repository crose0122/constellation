#!/usr/bin/env python3
"""Regression tests for the public-tree privacy boundary."""

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).with_name("leak_scan.py")
REPO_ROOT = MODULE_PATH.parent.parent
SPEC = importlib.util.spec_from_file_location("leak_scan", MODULE_PATH)
leak_scan = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(leak_scan)

# Adversarial 4M-character probes (including 1024 shared-prefix terms) must
# stay interactive; the local target is ~2s, with headroom for loaded CI.
LARGE_INPUT_BUDGET_SECONDS = 2.5


def _synthetic(text: str) -> str:
    """Make synthetic prose without storing scanner canaries verbatim."""
    return text.replace("~", " ")


def _address(*octets: int) -> str:
    return ".".join(str(octet) for octet in octets)


def _git_env(extra=None):
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("GIT_")}
    if extra:
        env.update(extra)
    return env


def _git(root: Path, *args: str, check=True, capture_output=False):
    return subprocess.run(
        ["git", *args], cwd=root, env=_git_env(), check=check,
        capture_output=capture_output,
    )


def _init_repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Synthetic Test")


class SemanticPrivacyCanaryTest(unittest.TestCase):
    def _scan_bytes(self, data: bytes, suffix: str = ".md", deny_terms=()) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / f"notes{suffix}"
            path.write_bytes(data)
            return leak_scan.scan([path], deny_terms=deny_terms)

    def _scan_text(self, text: str, suffix: str = ".md", deny_terms=()) -> list[str]:
        return self._scan_bytes(text.encode(), suffix=suffix, deny_terms=deny_terms)

    def test_configured_synthetic_name_is_rejected(self):
        hits = self._scan_text("Synthetic identity: ZephyrCanary.\n", deny_terms=("ZephyrCanary",))
        self.assertTrue(any("configured private term" in hit for hit in hits), hits)

    def test_configured_synthetic_phrase_is_rejected(self):
        hits = self._scan_text(
            "The sample is stored beside the Copper Comet shelf.\n",
            deny_terms=("Copper Comet",),
        )
        self.assertTrue(any("configured private term" in hit for hit in hits), hits)

    def test_configured_phrase_rejects_bounded_separator_variants(self):
        term = ("Copper Comet",)
        for separator in ("\n", "\0", "_", "-", " \t "):
            with self.subTest(separator=repr(separator)):
                payload = f"archive/Copper{separator}Comet/photos".encode()
                hits = self._scan_bytes(payload, suffix=".bin", deny_terms=term)
                self.assertTrue(any("configured private term" in hit for hit in hits), hits)

    def test_configured_phrase_separator_tolerance_is_bounded(self):
        payload = ("Copper" + ("_" * (leak_scan.MAX_DENY_SEPARATOR_LENGTH + 1)) + "Comet").encode()
        self.assertEqual(self._scan_bytes(payload, deny_terms=("Copper Comet",)), [])

    def test_configured_terms_normalize_accents_and_path_separators(self):
        term = ("Café House",)
        variants = (
            "Café House", "Cafe\u0301 House", "Café/House", "Café.House",
            "Café\u200bHouse", "Café\u2028House",
        )
        for variant in variants:
            with self.subTest(variant=ascii(variant)):
                hits = self._scan_text(f"archive/{variant}/photos\n", deny_terms=term)
                self.assertTrue(any("configured private term" in hit for hit in hits), hits)

    def test_configured_term_and_scanned_data_are_both_nfkc_normalized(self):
        hits = self._scan_text(
            "archive/Café.House/photos\n",
            deny_terms=("Cafe\u0301 House",),
        )
        self.assertTrue(any("configured private term" in hit for hit in hits), hits)

    def test_all_unicode_bidi_and_zero_width_format_controls_split_configured_terms(self):
        # Unicode Bidi_Control plus the zero-width Cf controls that can hide a
        # token boundary. Keep this inventory explicit so omissions are visible.
        separators = (
            "\u00ad",  # soft hyphen (normally invisible)
            "\u061c",  # Arabic letter mark
            "\u180e",  # Mongolian vowel separator
            "\u200b", "\u200c", "\u200d", "\u200e", "\u200f",
            "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",
            "\u2060", "\u2061", "\u2062", "\u2063", "\u2064",
            "\u2066", "\u2067", "\u2068", "\u2069",
            "\u206a", "\u206b", "\u206c", "\u206d", "\u206e", "\u206f",
            "\ufeff",
        )
        for separator in separators:
            with self.subTest(codepoint=f"U+{ord(separator):04X}"):
                hits = self._scan_text(
                    f"archive/Café{separator}House/photos\n",
                    deny_terms=("Café House",),
                )
                self.assertTrue(any("configured private term" in hit for hit in hits), hits)

    def test_unicode_slash_and_dot_lookalikes_split_configured_terms(self):
        separators = (
            "\u2044", "\u2215", "\u29f8", "\uff0f",  # slash forms
            "\u2024", "\u2027", "\u2219", "\u22c5", "\u3002",
            "\ufe52", "\uff0e", "\uff61",  # dot/full-stop forms
        )
        for separator in separators:
            with self.subTest(codepoint=f"U+{ord(separator):04X}"):
                hits = self._scan_text(
                    f"archive/Café{separator}House/photos\n",
                    deny_terms=("Café House",),
                )
                self.assertTrue(any("configured private term" in hit for hit in hits), hits)

    def test_max_deny_separator_length_is_pinned(self):
        # Widening or narrowing the visible-separator tolerance is a policy
        # change that must be reviewed explicitly, not drift silently.
        self.assertEqual(leak_scan.MAX_DENY_SEPARATOR_LENGTH, 8)

    def test_unicode_separator_tolerance_is_bounded_for_mixed_runs(self):
        # Invisible characters are deleted (or collapsed to one gap), so they
        # never count toward the bound; visible separators still do.
        visible = "\u2215\u2044\uff0f\u2024"
        run = (visible * leak_scan.MAX_DENY_SEPARATOR_LENGTH)[
            :leak_scan.MAX_DENY_SEPARATOR_LENGTH + 1
        ]
        payload = "Café" + "\u202e".join(run) + "\u2066House"
        self.assertEqual(self._scan_text(payload, deny_terms=("Café House",)), [])

    def test_long_invisible_runs_between_words_cannot_split_configured_terms(self):
        for invisible in ("\u200b", "\u202e", "\U000e0020", "\u3164"):
            with self.subTest(codepoint=f"U+{ord(invisible):04X}"):
                payload = "Café" + invisible * (leak_scan.MAX_DENY_SEPARATOR_LENGTH * 4) + "House"
                hits = self._scan_text(payload, deny_terms=("Café House",))
                self.assertTrue(any("configured private term" in hit for hit in hits), hits)

    def test_default_ignorable_ranges_are_pinned_to_unicode_definition(self):
        # DerivedCoreProperties.txt, Default_Ignorable_Code_Point (Unicode 15/16),
        # coalesced. Any edit to the table must be a deliberate policy change.
        expected = (
            (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C),
            (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180F),
            (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F),
            (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF),
            (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3),
            (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF),
        )
        self.assertEqual(leak_scan.DEFAULT_IGNORABLE_RANGES, expected)

    def test_default_ignorable_ranges_match_an_independent_unicode_database(self):
        perl = shutil.which("perl")
        if not perl:
            self.skipTest("perl unavailable for independent UCD cross-check")
        script = (
            "my @r; my $s; for my $c (0..0x10FFFF) {"
            " next if $c >= 0xD800 && $c <= 0xDFFF;"
            " my $m = chr($c) =~ /\\p{Default_Ignorable_Code_Point}/;"
            " if ($m && !defined $s) { $s = $c }"
            " if (!$m && defined $s) { push @r, \"$s-\" . ($c - 1); undef $s } }"
            " print join(' ', @r);"
        )
        result = subprocess.run([perl, "-e", script], capture_output=True, text=True, check=True)
        observed = tuple(
            tuple(int(part) for part in item.split("-"))
            for item in result.stdout.split()
        )
        self.assertEqual(observed, leak_scan.DEFAULT_IGNORABLE_RANGES)

    def test_invisible_characters_inside_words_cannot_hide_configured_terms(self):
        in_word = (
            "\u200b",  # zero width space
            "\u00ad",  # soft hyphen
            "\u202e",  # right-to-left override
            "\u2062",  # invisible times
            "\u034f",  # combining grapheme joiner
            "\ufe0f",  # variation selector-16
            "\U000e0001", "\U000e0020", "\U000e007f",  # tag characters
            "\u3164", "\u115f", "\u1160", "\uffa0",  # Hangul fillers
            "\u180b", "\u17b4", "\ufff0", "\U0001bca0", "\U0001d173", "\U000e0100",
        )
        for invisible in in_word:
            with self.subTest(codepoint=f"U+{ord(invisible):04X}"):
                for payload in (
                    f"Ca{invisible}fé House",
                    f"Café Ho{invisible}use",
                    f"C{invisible}a{invisible}f{invisible}é{invisible} {invisible}House",
                ):
                    hits = self._scan_text(f"archive/{payload}/photos\n", deny_terms=("Café House",))
                    self.assertTrue(
                        any("configured private term" in hit for hit in hits),
                        (ascii(payload), hits),
                    )

    def test_marker_free_and_marker_aware_patterns_agree_on_visible_text(self):
        terms = ("Café House", "Copper Comet", "ZephyrCanary")
        plain = leak_scan._deny_pattern(terms, False)
        aware = leak_scan._deny_pattern(terms, True)
        for text in ("café house", "copper__comet", "x zephyrcanary y", "cafe house", "copper cometx"):
            with self.subTest(text=text):
                self.assertEqual(bool(plain.search(text)), bool(aware.search(text)))

    def test_invisible_character_before_combining_mark_still_composes(self):
        hits = self._scan_text("Cafe\u200b\u0301 House\n", deny_terms=("Café House",))
        self.assertTrue(any("configured private term" in hit for hit in hits), hits)

    def test_invisible_run_before_nfkc_derived_combining_mark_still_composes(self):
        # U+FF9E/U+FF9F are not category M themselves; they only become
        # combining voiced marks after NFKC, so the trigger set must be
        # derived from each code point's NFKC form.
        cases = (
            ("\uff76{inv}\uff9e", "\u30ac"),                      # ｶﾞ -> ガ
            ("\uff7b{inv}\uff9e\uff84\uff73", "\u30b6\u30c8\u30a6"),  # ｻﾞﾄｳ -> ザトウ
            ("\uff8a{inv}\uff9f", "\u30d1"),                      # ﾊﾟ -> パ
            ("\u314e{inv}\u314f", "\ud558"),                      # ㅎㅏ -> 하
        )
        for invisible in ("\u200b", "\ufe0f"):
            for template, term in cases:
                payload = template.format(inv=invisible)
                with self.subTest(payload=ascii(payload)):
                    hits = self._scan_text(f"archive/{payload}/photos\n", deny_terms=(term,))
                    self.assertTrue(any("configured private term" in hit for hit in hits), hits)

    def test_nfkc_derived_trigger_set_covers_every_combining_start(self):
        _, _, before_joining, _ = leak_scan._canonical_tables()
        for codepoint in range(0x110000):
            character = chr(codepoint)
            if character in leak_scan.INVISIBLE_CHARACTERS:
                continue
            folded = unicodedata.normalize("NFKC", character)
            if folded and (
                unicodedata.category(folded[0]).startswith("M")
                or 0x1161 <= ord(folded[0]) <= 0x11FF
                or 0xD7B0 <= ord(folded[0]) <= 0xD7FF
            ):
                with self.subTest(codepoint=f"U+{codepoint:04X}"):
                    self.assertEqual(before_joining.sub("", "x\0" + character), "x" + character)

    def test_invisible_run_inside_decomposed_hangul_syllable_still_composes(self):
        # Pins the Hangul medial/final jamo trigger range (U+1161-U+11FF).
        hits = self._scan_text("archive/\u1112\u200b\u1161\u11ab/photos\n", deny_terms=("\ud55c",))
        self.assertTrue(any("configured private term" in hit for hit in hits), hits)
        hits = self._scan_text("archive/\u1112\u1161\u200b\u11ab/photos\n", deny_terms=("\ud55c",))
        self.assertTrue(any("configured private term" in hit for hit in hits), hits)

    def test_invisible_characters_inside_deny_terms_vanish(self):
        # Pins the marker removal in _term_pieces.
        hits = self._scan_text("archive/Maplewood/photos\n", deny_terms=("Maple\u200bwood",))
        self.assertTrue(any("configured private term" in hit for hit in hits), hits)

    def test_blank_looking_fillers_act_as_separators(self):
        for filler in ("\u3164", "\u115f", "\u1160", "\u2800", "\uffa0"):
            with self.subTest(codepoint=f"U+{ord(filler):04X}"):
                hits = self._scan_text(f"archive/Café{filler}House/photos\n", deny_terms=("Café House",))
                self.assertTrue(any("configured private term" in hit for hit in hits), hits)

    def test_invisible_deletion_keeps_token_boundaries_for_benign_superstrings(self):
        for payload in (
            "SuperCa\u200bfé Houseboat stays benign.\n",
            "Ca\u200bfés Housing stays benign.\n",
        ):
            with self.subTest(payload=ascii(payload)):
                self.assertEqual(self._scan_text(payload, deny_terms=("Café House",)), [])

    def test_large_inputs_are_scanned_within_bounded_time(self):
        size = leak_scan.MAX_NORMALIZED_CHARS - 16
        many_terms = tuple(f"Term{index:04d} Word{index}" for index in range(leak_scan.MAX_DENY_TERMS))
        widest_terms = tuple(
            " ".join(f"w{index:04d}x{word:02d}" for word in range(24))[:leak_scan.MAX_DENY_TERM_CHARS]
            for index in range(leak_scan.MAX_DENY_TERMS)
        )
        cases = (
            ("plain", b"x" * size, ("Café House",)),
            ("invisible-dense", ("\u200bx" * (size // 2)).encode(), ("Café House",)),
            ("shared-prefix", ("term0 " * (size // 6)).encode(), many_terms),
            ("invisible-prefix", ("t\u200be\u200br\u200bm " * (size // 8)).encode(), many_terms),
            ("widest-denylist", ("w0001x01 " * (size // 9)).encode(), widest_terms),
        )
        for name, data, terms in cases:
            with self.subTest(case=name):
                leak_scan._deny_pattern.cache_clear()
                started = time.perf_counter()
                hits = leak_scan.scan_bytes("candidate", data, terms)
                elapsed = time.perf_counter() - started
                self.assertEqual(hits, [])
                self.assertLess(elapsed, LARGE_INPUT_BUDGET_SECONDS, f"{name}: {elapsed:.2f}s")

    def test_widest_denylist_with_invisible_text_has_bounded_compile_and_scan(self):
        # In-word invisible tolerance multiplies pattern size; that pattern is
        # compiled once per denylist (cached) and only when text contains an
        # invisible run. Bound the one-time compile and the per-blob scan.
        size = leak_scan.MAX_NORMALIZED_CHARS - 16
        widest_terms = tuple(
            " ".join(f"w{index:04d}x{word:02d}" for word in range(24))[:leak_scan.MAX_DENY_TERM_CHARS]
            for index in range(leak_scan.MAX_DENY_TERMS)
        )
        data = ("\u200b" + "w0001x01 " * (size // 9)).encode()
        leak_scan._deny_pattern.cache_clear()
        started = time.perf_counter()
        leak_scan._deny_pattern(widest_terms, True)
        compile_elapsed = time.perf_counter() - started
        started = time.perf_counter()
        hits = leak_scan.scan_bytes("candidate", data, widest_terms)
        scan_elapsed = time.perf_counter() - started
        self.assertEqual(hits, [])
        self.assertLess(compile_elapsed, 2 * LARGE_INPUT_BUDGET_SECONDS, f"compile {compile_elapsed:.2f}s")
        self.assertLess(scan_elapsed, LARGE_INPUT_BUDGET_SECONDS, f"scan {scan_elapsed:.2f}s")

    def test_unicode_normalization_retains_benign_superstrings_and_nonseparator_controls(self):
        for payload in (
            "SuperCafe\u0301 Houseboat stays benign.\n",
            "Café\u0600House is not a separator-delimited phrase.\n",
            "Café\ufff9House is not a separator-delimited phrase.\n",
        ):
            with self.subTest(payload=ascii(payload)):
                self.assertEqual(self._scan_text(payload, deny_terms=("Café House",)), [])

    def test_denylist_rejects_oversized_terms_and_too_many_terms(self):
        cases = (
            ("x" * (leak_scan.MAX_DENY_TERM_CHARS + 1)) + "\n",
            "x\n" * (leak_scan.MAX_DENY_TERMS + 1),
        )
        for content in cases:
            with self.subTest(size=len(content)), tempfile.TemporaryDirectory() as directory:
                denylist = Path(directory) / "denylist.txt"
                denylist.write_text(content, encoding="utf-8")
                with self.assertRaises(leak_scan.DenylistError):
                    leak_scan.load_deny_terms(denylist)

    def test_oversized_normalized_blob_fails_closed(self):
        data = b"x" * (leak_scan.MAX_NORMALIZED_CHARS + 1)
        hits = leak_scan.scan_bytes("candidate", data, ("Café House",))
        self.assertTrue(any("normalized scan limit" in hit for hit in hits), hits)

    def test_configured_term_matches_before_common_path_labels(self):
        for separator in ("_", "-"):
            with self.subTest(separator=separator):
                hits = self._scan_text(
                    f"/media/ZephyrCanary{separator}photos/latest.jpg\n",
                    deny_terms=("ZephyrCanary",),
                )
                self.assertTrue(any("configured private term" in hit for hit in hits), hits)

    def test_configured_path_label_is_rejected_without_storage_context(self):
        hits = self._scan_text(
            "/run/media/user/ZephyrCanary/photos\n",
            deny_terms=("ZephyrCanary",),
        )
        self.assertTrue(any("configured private term" in hit for hit in hits), hits)

    def test_external_denylist_loads_synthetic_terms(self):
        with tempfile.TemporaryDirectory() as directory:
            denylist = Path(directory) / "denylist.txt"
            denylist.write_text("# mounted private terms\nZephyrCanary\nCopper Comet\n", encoding="utf-8")
            self.assertEqual(
                leak_scan.load_deny_terms(denylist),
                ("ZephyrCanary", "Copper Comet"),
            )

    def test_missing_configured_denylist_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.txt"
            with self.assertRaises(leak_scan.DenylistError):
                leak_scan.load_deny_terms(missing)

    def test_unreadable_configured_denylist_fails_closed(self):
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            with self.assertRaises(leak_scan.DenylistError) as raised:
                leak_scan.load_deny_terms("outside-checkout.txt")
        self.assertNotIn("outside-checkout", str(raised.exception))

    def test_empty_comments_only_and_malformed_denylists_fail_closed(self):
        cases = {
            "empty": b"",
            "comments": b"# mounted terms\n  # another comment\n",
            "nul": b"Copper\0Comet\n",
            "no-alphanumeric-term": b"---___\n",
            "invalid-utf8": b"\xff\xfe\n",
            "utf8-bom": b"\xef\xbb\xbfZephyrCanary\n",
        }
        for name, content in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                denylist = Path(directory) / "denylist.txt"
                denylist.write_bytes(content)
                with self.assertRaises(leak_scan.DenylistError):
                    leak_scan.load_deny_terms(denylist)

    def test_required_private_denylist_gate_rejects_missing_configuration(self):
        env = _git_env()
        env.pop(leak_scan.DENYLIST_ENV, None)
        result = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--require-private-denylist"],
            env=env, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("required private denylist is not configured", result.stderr)

    def test_required_private_denylist_gate_does_not_print_terms(self):
        with tempfile.TemporaryDirectory() as directory:
            denylist = Path(directory) / "denylist.txt"
            secret = "ZephyrCanary"
            denylist.write_text(secret + "\n", encoding="utf-8")
            candidate = Path(directory) / "candidate.txt"
            candidate.write_text("/home/" + secret + "/private\n", encoding="utf-8")
            env = _git_env({leak_scan.DENYLIST_ENV: str(denylist)})
            result = subprocess.run(
                [sys.executable, str(MODULE_PATH), "--require-private-denylist", str(candidate)],
                env=env, capture_output=True, text=True,
            )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn(secret, result.stdout + result.stderr)

    def test_ci_and_pre_push_release_paths_require_private_denylist(self):
        workflow = (REPO_ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
        hook = (REPO_ROOT / ".githooks/pre-push").read_text(encoding="utf-8")
        command = "python3 tools/leak_scan.py --require-private-denylist"
        self.assertIn(command, workflow)
        self.assertIn(command, hook)
        self.assertLess(workflow.index("python3 -m unittest -v tools/test_leak_scan.py"),
                        workflow.index("secrets.CONSTELLATION_PRIVATE_DENYLIST"),
                        "public synthetic tests must run before any private-secret gate")

    def test_all_rfc1918_ranges_are_rejected(self):
        for address in (
            _address(10, 23, 45, 67),
            _address(172, 16, 0, 1),
            _address(172, 31, 255, 254),
            _address(192, 168, 44, 9),
        ):
            with self.subTest(address=address):
                hits = self._scan_text(f"endpoint={address}\n")
                self.assertTrue(any("private address/path" in hit for hit in hits), hits)

    def test_link_local_and_noncanonical_loopback_are_rejected(self):
        for address in (_address(169, 254, 20, 30), _address(127, 0, 0, 2)):
            with self.subTest(address=address):
                hits = self._scan_text(f"endpoint={address}\n")
                self.assertTrue(any("private address/path" in hit for hit in hits), hits)

    def test_ipv6_link_local_and_unique_local_are_rejected(self):
        for address in (f"fe{80}::1234", f"fd{12}:3456::7"):
            with self.subTest(address=address):
                hits = self._scan_text(f"endpoint=[{address}]\n")
                self.assertTrue(any("private address/path" in hit for hit in hits), hits)

    def test_concrete_storage_mount_paths_are_rejected(self):
        for sample in (
            "/mnt/" + "data",
            "/mnt/" + "data/Library",
            "/MNT/" + "NVME/archive",
            "Path=/mnt/" + "nvme\n",
        ):
            with self.subTest(sample=sample):
                hits = self._scan_text(f"library={sample}\n")
                self.assertTrue(any("private address/path" in hit for hit in hits), hits)

    def test_storage_mount_unit_dependency_is_rejected(self):
        for unit in ("mnt-" + "data.mount", "mnt-" + "nvme.mount"):
            with self.subTest(unit=unit):
                hits = self._scan_text(f"[Unit]\nAfter=network.target {unit}\n")
                self.assertTrue(any("private address/path" in hit for hit in hits), hits)

    def test_generic_storage_examples_are_allowed(self):
        self.assertEqual(self._scan_text(
            "library=/mnt/library/photos vault=/mnt/vault /mnt/<source> /mnt/dataset\n"
            "After=network-online.target local-fs.target\n"
        ), [])

    def test_documentation_address_and_canonical_localhost_are_allowed(self):
        self.assertEqual(self._scan_text("examples: 192.0.2.10 and 127.0.0.1\n"), [])

    def test_role_narration_split_across_lines_is_rejected(self):
        hits = self._scan_text("The project\npatron approved this.\n")
        self.assertTrue(any("role narration" in hit for hit in hits), hits)

    def test_role_narration_split_by_nul_is_rejected(self):
        hits = self._scan_bytes(b"The project\0patron approved this.\n", suffix=".bin")
        self.assertTrue(any("role narration" in hit for hit in hits), hits)

    def test_attributed_speech_split_across_lines_is_rejected(self):
        hits = self._scan_text('Maintainer said:\n"Use the quiet mode."\n')
        self.assertTrue(any("attributed quoted speech" in hit for hit in hits), hits)

    def test_concrete_household_topology_split_by_nul_is_rejected(self):
        canary = _synthetic("The~archive~host~runs~storage~and~inference~on~port~7331.")
        hits = self._scan_bytes(canary.replace(" host ", " host\0").encode(), suffix=".dat")
        self.assertTrue(any("concrete topology" in hit for hit in hits), hits)

    def test_private_relationship_split_across_lines_is_rejected(self):
        hits = self._scan_text("The maintainer's sibling's\nhousehold will join the pilot.\n")
        self.assertTrue(any("private relationship" in hit for hit in hits), hits)

    def test_arbitrary_extension_and_nul_bytes_do_not_bypass_scan(self):
        payload = b"prefix\0" + _synthetic("The~project~patron~approved~this.\n").encode()
        hits = self._scan_bytes(payload, suffix=".png")
        self.assertTrue(any("role narration" in hit for hit in hits), hits)

    def test_broken_redaction_placeholder_is_rejected(self):
        hits = self._scan_text(_synthetic("Connect~to~http://the~archive~node:7331/.\n"))
        self.assertTrue(any("broken redaction placeholder" in hit for hit in hits), hits)

    def test_live_library_and_vault_state_is_rejected(self):
        hits = self._scan_text(_synthetic(
            "Production~library:~12,345~photos~ingested;~vault~exists.\n"
        ))
        self.assertTrue(any("live household state" in hit for hit in hits), hits)

    def test_missing_explicit_path_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            hits = leak_scan.scan([Path(directory) / "missing.data"])
        self.assertTrue(any("missing requested path" in hit for hit in hits), hits)

    def test_public_candidate_worktree_passes_public_rules(self):
        hits, count = leak_scan.scan_git_state("worktree", REPO_ROOT)
        self.assertGreater(count, 0)
        self.assertEqual(hits, [])

    def test_scanner_source_does_not_flag_itself(self):
        self.assertEqual(leak_scan.scan([MODULE_PATH]), [])

    def test_configured_term_requires_token_boundaries(self):
        self.assertEqual(
            self._scan_text("The zephyrcanaryish fixture is benign.\n", deny_terms=("ZephyrCanary",)),
            [],
        )
        self.assertEqual(
            self._scan_text("A SuperZephyrCanary fixture is benign.\n", deny_terms=("ZephyrCanary",)),
            [],
        )
        self.assertEqual(
            self._scan_text("Unicode éZephyrCanary and ZephyrCanary９ stay benign.\n",
                            deny_terms=("ZephyrCanary",)),
            [],
        )

    def test_generated_source_is_scanned_as_bytes(self):
        hits = self._scan_text(
            _synthetic("//~The~project~patron~approved~this.\n"),
            suffix=".generated.js",
        )
        self.assertTrue(any("role narration" in hit for hit in hits), hits)

    def test_structural_ratchet_fails_if_detector_is_disabled(self):
        canary = f"endpoint={_address(10, 254, 253, 252)}\n"
        hits = self._scan_text(canary)
        self.assertTrue(any("private address/path" in hit for hit in hits), hits)
        with mock.patch.object(leak_scan, "STRUCTURAL", re.compile(r"(?!)")):
            mutated = self._scan_text(canary)
        self.assertFalse(any("private address/path" in hit for hit in mutated), mutated)


class GitStateTest(unittest.TestCase):
    def _filename_only_hits(self, state, name, *, commit=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            path = root / name
            path.write_text("Generic public fixture.\n", encoding="utf-8")
            if state in {"head", "index", "worktree"}:
                _git(root, "add", "--", name)
            if commit:
                _git(root, "commit", "-qm", "filename fixture")
            return leak_scan.scan_git_state(state, root, deny_terms=("Café House",))

    def test_configured_term_in_head_index_worktree_and_untracked_filenames_is_rejected(self):
        cases = (
            ("head", "Cafe\u0301.House.txt", True),
            ("index", "Café-House.txt", False),
            ("worktree", "Café_House.txt", False),
            ("untracked", "Café\u200bHouse.txt", False),
        )
        for state, name, commit in cases:
            with self.subTest(state=state):
                (hits, count) = self._filename_only_hits(state, name, commit=commit)
                self.assertEqual(count, 1)
                self.assertTrue(any("configured private term in filename" in hit for hit in hits), hits)
                self.assertNotIn("Café", "\n".join(hits))
                self.assertNotIn("Cafe", "\n".join(hits))

    def test_listed_unicode_bypasses_are_rejected_in_four_git_filename_states(self):
        cases = (
            ("head", "Café\u202eHouse.txt", True),
            ("index", "Café\u200eHouse.txt", False),
            ("worktree", "Café\u2066House.txt", False),
            ("untracked", "Café\u2215House.txt", False),
            ("untracked", "Café\u2044House.txt", False),
        )
        for state, name, commit in cases:
            with self.subTest(state=state, name=ascii(name)):
                hits, count = self._filename_only_hits(state, name, commit=commit)
                self.assertEqual(count, 1)
                self.assertTrue(any("configured private term in filename" in hit for hit in hits), hits)
                self.assertNotIn("Café", "\n".join(hits))

    def test_in_word_invisible_bypasses_are_rejected_in_four_git_filename_states(self):
        cases = (
            ("head", "Ca\u200bfé House.txt", True),
            ("index", "Café Ho\u00adu\u202ese.txt", False),
            ("worktree", "C\u034fa\ufe0ffé\u2062House.txt", False),
            ("untracked", "Ca\U000e0020fé\u2800Hou\U000e0001se.txt", False),
            ("untracked", "Café\u3164Ho\u115fuse.txt", False),
        )
        for state, name, commit in cases:
            with self.subTest(state=state, name=ascii(name)):
                hits, count = self._filename_only_hits(state, name, commit=commit)
                self.assertEqual(count, 1)
                self.assertTrue(any("configured private term in filename" in hit for hit in hits), hits)
                self.assertNotIn("fé", "\n".join(hits))

    def test_filename_and_content_detection_agree_on_invisible_variants(self):
        variants = (
            "Ca\u200bfé House", "Café\u200bHouse", "Ca\u00adfé\u3164House",
            "Ca\U000e0041fé\u2800House", "Cafe\u034f\u0301 Ho\ufe0fuse",
        )
        for variant in variants:
            with self.subTest(variant=ascii(variant)), tempfile.TemporaryDirectory() as directory:
                named = Path(directory) / f"{variant}.txt"
                named.write_text("Generic public fixture.\n", encoding="utf-8")
                content = Path(directory) / "content.txt"
                content.write_text(f"archive/{variant}/photos\n", encoding="utf-8")
                hits = leak_scan.scan([named, content], deny_terms=("Café House",))
                self.assertTrue(any("configured private term in filename" in hit for hit in hits), hits)
                self.assertTrue(any("content.txt:1: configured private term" in hit for hit in hits), hits)

    def test_halfwidth_kana_voiced_mark_split_is_rejected_in_filename_and_content(self):
        cases = (
            ("\uff76{inv}\uff9e", "\u30ac"),
            ("\uff7b{inv}\uff9e\uff84\uff73", "\u30b6\u30c8\u30a6"),
            ("\uff8a{inv}\uff9f", "\u30d1"),
        )
        for invisible in ("\u200b", "\ufe0f"):
            for template, term in cases:
                variant = template.format(inv=invisible)
                with self.subTest(variant=ascii(variant)), tempfile.TemporaryDirectory() as directory:
                    named = Path(directory) / f"{variant}.txt"
                    named.write_text("Generic public fixture.\n", encoding="utf-8")
                    content = Path(directory) / "content.txt"
                    content.write_text(f"archive/{variant}/photos\n", encoding="utf-8")
                    hits = leak_scan.scan([named, content], deny_terms=(term,))
                    self.assertTrue(any("configured private term in filename" in hit for hit in hits), hits)
                    self.assertTrue(any("content.txt:1: configured private term" in hit for hit in hits), hits)
                    self.assertNotIn(variant, "\n".join(hits))

    def test_representative_unicode_separator_set_is_rejected_in_requested_filenames(self):
        separators = "\u00ad\u061c\u180e\u200f\u202a\u202e\u2062\u2068\u2069\u206a\u206f\u2044\u2215\u29f8\u2024\u2027\u2219\u22c5\u3002\ufe52\uff61\ufeff"
        for separator in separators:
            with self.subTest(codepoint=f"U+{ord(separator):04X}"), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / f"Café{separator}House.txt"
                path.write_text("Generic public fixture.\n", encoding="utf-8")
                hits = leak_scan.scan([path], deny_terms=("Café House",))
                self.assertTrue(any("configured private term in filename" in hit for hit in hits), hits)
                self.assertNotIn("Café", "\n".join(hits))

    def test_nested_head_filename_is_scanned_recursively(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            nested = root / "nested"
            nested.mkdir()
            path = nested / "Café.House.txt"
            path.write_text("Generic public fixture.\n", encoding="utf-8")
            _git(root, "add", "--", "nested")
            _git(root, "commit", "-qm", "nested filename fixture")
            hits, count = leak_scan.scan_git_state("head", root, deny_terms=("Café House",))
        self.assertEqual(count, 1)
        self.assertTrue(any("configured private term in filename" in hit for hit in hits), hits)
        self.assertNotIn("Café", "\n".join(hits))

    def test_raw_invalid_utf8_filename_bytes_are_scanned_without_disclosure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            raw_name = b"Caf\xff-Cafe.House.dat"
            fd = os.open(os.fsencode(root) + b"/" + raw_name, os.O_WRONLY | os.O_CREAT, 0o600)
            os.write(fd, b"Generic public fixture.\n")
            os.close(fd)
            hits, count = leak_scan.scan_git_state("untracked", root, deny_terms=("Café House", "Cafe House"))
        self.assertEqual(count, 1)
        self.assertTrue(any("configured private term in filename" in hit for hit in hits), hits)
        self.assertNotIn("Cafe", "\n".join(hits))

    def test_rename_and_deletion_cannot_mask_unsafe_head_or_index_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            unsafe = root / "Café.House.txt"
            safe = root / "public.txt"
            unsafe.write_text("Generic public fixture.\n", encoding="utf-8")
            _git(root, "add", "--", unsafe.name)
            _git(root, "commit", "-qm", "unsafe historical filename")
            unsafe.rename(safe)
            _git(root, "add", "-A")
            head_hits, _ = leak_scan.scan_git_state("head", root, deny_terms=("Café House",))
            index_hits, _ = leak_scan.scan_git_state("index", root, deny_terms=("Café House",))
            self.assertTrue(any("configured private term in filename" in hit for hit in head_hits), head_hits)
            self.assertEqual(index_hits, [])

            _git(root, "reset", "-q", "HEAD")
            unsafe.unlink(missing_ok=True)
            index_hits, _ = leak_scan.scan_git_state("index", root, deny_terms=("Café House",))
            worktree_hits, _ = leak_scan.scan_git_state("worktree", root, deny_terms=("Café House",))
        self.assertTrue(any("configured private term in filename" in hit for hit in index_hits), index_hits)
        self.assertTrue(any("configured private term in filename" in hit for hit in worktree_hits), worktree_hits)
        self.assertFalse(any("Café" in hit for hit in index_hits + worktree_hits), index_hits + worktree_hits)
    def test_head_index_and_worktree_are_independent_and_newline_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            path = root / "line\nbreak.data"
            path.write_text("Generic release note.\n", encoding="utf-8")
            _git(root, "add", "--", path.name)
            _git(root, "commit", "-qm", "base")
            path.write_text(_synthetic("The~project~patron~approved~this.\n"), encoding="utf-8")
            worktree_hits, worktree_count = leak_scan.scan_git_state("worktree", root)
            head_hits, head_count = leak_scan.scan_git_state("head", root)
            _git(root, "add", "--", path.name)
            index_hits, index_count = leak_scan.scan_git_state("index", root)
        self.assertTrue(any("role narration" in hit for hit in worktree_hits), worktree_hits)
        self.assertTrue(any("role narration" in hit for hit in index_hits), index_hits)
        self.assertEqual(head_hits, [])
        self.assertEqual((worktree_count, index_count, head_count), (1, 1, 1))

    def test_subdirectory_invocation_still_scans_repository_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            (root / "nested").mkdir()
            (root / "release.md").write_text(
                _synthetic("The~project~patron~approved~this.\n"), encoding="utf-8"
            )
            _git(root, "add", "release.md")
            hits, count = leak_scan.scan_git_state("index", root / "nested")
        self.assertEqual(count, 1)
        self.assertTrue(any("role narration" in hit for hit in hits), hits)

    def test_unmerged_index_entry_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            path = root / "release.md"
            path.write_text("base\n", encoding="utf-8")
            _git(root, "add", "release.md")
            _git(root, "commit", "-qm", "base")
            _git(root, "checkout", "-qb", "other")
            path.write_text("other\n", encoding="utf-8")
            _git(root, "commit", "-qam", "other")
            _git(root, "checkout", "-q", "master")
            path.write_text("master\n", encoding="utf-8")
            _git(root, "commit", "-qam", "master")
            _git(root, "merge", "other", check=False, capture_output=True)
            hits, _ = leak_scan.scan_git_state("index", root)
        self.assertTrue(any("unmerged index entry" in hit for hit in hits), hits)

    def test_unmerged_index_filename_is_scanned_and_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            path = root / "Café.House.txt"
            path.write_text("base\n", encoding="utf-8")
            _git(root, "add", "--", path.name)
            _git(root, "commit", "-qm", "base")
            _git(root, "checkout", "-qb", "other")
            path.write_text("other\n", encoding="utf-8")
            _git(root, "commit", "-qam", "other")
            _git(root, "checkout", "-q", "master")
            path.write_text("master\n", encoding="utf-8")
            _git(root, "commit", "-qam", "master")
            _git(root, "merge", "other", check=False, capture_output=True)
            hits, count = leak_scan.scan_git_state("index", root, deny_terms=("Café House",))
        self.assertGreaterEqual(count, 1)
        self.assertTrue(any("configured private term in filename" in hit for hit in hits), hits)
        self.assertNotIn("Café", "\n".join(hits))

    def test_worktree_symlink_scans_link_blob_not_external_target(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            _init_repo(root)
            target = Path(outside) / "external.data"
            target.write_text(_synthetic("The~project~patron~approved~this.\n"), encoding="utf-8")
            (root / "linked.data").symlink_to(target)
            _git(root, "add", "linked.data")
            hits, count = leak_scan.scan_git_state("worktree", root)
        self.assertEqual(count, 1)
        self.assertEqual(hits, [])

    def test_unsafe_symlink_target_text_is_scanned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            (root / "linked.data").symlink_to(_synthetic("the~project~patron"))
            _git(root, "add", "linked.data")
            hits, count = leak_scan.scan_git_state("worktree", root)
        self.assertEqual(count, 1)
        self.assertTrue(any("role narration" in hit for hit in hits), hits)

    def test_untracked_discovery_ratchet_scans_binary_with_newline_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            generated = root / "generated\nasset.bin"
            generated.write_bytes(
                b"synthetic\0" + _synthetic("The~project~patron~approved~this.\n").encode()
            )
            hits, count = leak_scan.scan_git_state("untracked", root)
        self.assertEqual(count, 1, "removing untracked discovery must fail this ratchet")
        self.assertTrue(any("role narration" in hit for hit in hits), hits)
        self.assertTrue(any("\\n" in hit for hit in hits), hits)

    def test_safe_worktree_cannot_mask_unsafe_head_and_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            path = root / "policy.data"
            path.write_text(_synthetic("The~project~patron~approved~this.\n"), encoding="utf-8")
            _git(root, "add", "policy.data")
            _git(root, "commit", "-qm", "synthetic unsafe base")
            path.write_text("Public generic guidance.\n", encoding="utf-8")
            head_hits, _ = leak_scan.scan_git_state("head", root)
            index_hits, _ = leak_scan.scan_git_state("index", root)
            worktree_hits, _ = leak_scan.scan_git_state("worktree", root)
        self.assertTrue(any("role narration" in hit for hit in head_hits), head_hits)
        self.assertTrue(any("role narration" in hit for hit in index_hits), index_hits)
        self.assertEqual(worktree_hits, [])

    def test_hostile_git_routing_environment_is_ignored_and_sentinel_unchanged(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as decoy:
            root, decoy_root = Path(directory), Path(decoy)
            _init_repo(root)
            _init_repo(decoy_root)
            (root / "policy.data").write_text(
                _synthetic("The~project~patron~approved~this.\n"), encoding="utf-8"
            )
            _git(root, "add", "policy.data")
            sentinel = decoy_root / "sentinel.data"
            sentinel.write_text("SENTINEL-CONTENT\n", encoding="utf-8")
            _git(decoy_root, "add", "sentinel.data")
            before = sentinel.read_bytes()
            env = _git_env({
                "GIT_DIR": str(decoy_root / ".git"),
                "GIT_WORK_TREE": str(decoy_root),
                "GIT_INDEX_FILE": str(decoy_root / ".git" / "index"),
            })
            result = subprocess.run(
                [sys.executable, str(MODULE_PATH), "--state", "index"],
                cwd=root, env=env, capture_output=True, text=True,
            )
            after = sentinel.read_bytes()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("role narration", result.stdout)
        self.assertEqual(after, before)

    def test_discovery_failure_is_reported_fail_closed(self):
        failure = subprocess.CalledProcessError(128, ["git", "rev-parse"])
        with mock.patch.object(leak_scan, "_run_git", side_effect=failure):
            hits, count = leak_scan.scan_git_state("index", ".")
        self.assertEqual(count, 0)
        self.assertTrue(any("git discovery failed" in hit for hit in hits), hits)

    def test_blob_read_failure_is_reported_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_repo(root)
            (root / "safe.data").write_text("Generic.\n", encoding="utf-8")
            _git(root, "add", "safe.data")
            with mock.patch.object(
                leak_scan, "_git_blob", side_effect=leak_scan.GitScanError("synthetic blob failure")
            ):
                hits, count = leak_scan.scan_git_state("index", root)
        self.assertEqual(count, 1)
        self.assertTrue(any("blob read failed" in hit for hit in hits), hits)

    def test_git_timeout_is_reported_fail_closed(self):
        timeout = subprocess.TimeoutExpired(["git", "rev-parse"], leak_scan.GIT_TIMEOUT_SECONDS)
        with mock.patch.object(leak_scan, "_run_git", side_effect=timeout):
            hits, count = leak_scan.scan_git_state("head", ".")
        self.assertEqual(count, 0)
        self.assertTrue(any("timed out" in hit for hit in hits), hits)


if __name__ == "__main__":
    unittest.main()
