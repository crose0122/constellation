# Public Repository Privacy Policy

This repository contains product code, neutral specifications, ADRs, synthetic fixtures, and generic deployment examples. It must not contain raw interviews, private deliberation, attributed speech, household or relationship details, live library/vault state, real hostnames or addresses, storage labels, service co-location, deployment evidence, or internal tracker narration. Keep those records in an access-controlled private system. Public examples use synthetic identities and documentation-only addresses.

## Required private certification

Household-specific deny terms must exist only in an external, access-controlled newline-delimited file. The repository stores neither those terms nor their hashes. Trusted maintainer CI provides the complete file through the `CONSTELLATION_PRIVATE_DENYLIST` repository secret; the workflow writes it to a mode-restricted file outside the checkout and runs `python3 tools/leak_scan.py --require-private-denylist`. Maintainers running the pre-push release gate must point `CONSTELLATION_PRIVATE_DENYLIST` at the same class of access-controlled file before pushing.

The public synthetic scanner regression tests run without that secret on every push and pull request. Fork pull requests do not receive repository secrets: their required private-certification step must remain failed/unavailable, never be interpreted as a clean privacy result. Only a trusted maintainer run with a present, readable, non-empty, well-formed private denylist can produce a green privacy-certification job. Scanner diagnostics identify configuration classes and finding locations but never print configured terms.

Every candidate is scanned across HEAD, the index, the tracked worktree, and non-ignored untracked files. A clean automated scan is necessary but does not replace independent semantic review of the complete tree. Review the branch itself, not only its diff, because a public pull-request head is already exposed.

## Deny-term matching coverage

Filenames and blob contents use the same canonicalization. Text is NFKC-normalized and casefolded. Every Unicode `Default_Ignorable_Code_Point` (the official ranges are pinned in `tools/leak_scan.py`) plus bidi/zero-width format controls is treated as invisible: a run of them matches both as deleted (inside a word) and as a single word gap (between words). Whitespace, Unicode separators, `_ - / \ .`, blank-looking fillers (Hangul fillers, braille blank), and slash/dot lookalikes are visible separators, tolerated up to eight in a row between the words of a multi-word term. An invisible run directly before any code point whose NFKC form starts with a combining mark or a Hangul medial/final jamo (for example halfwidth kana voiced marks U+FF9E/U+FF9F and Hangul compatibility vowels) is deleted before normalization, so the pieces still compose (`ｶ` + ZWSP + `ﾞ` still matches `ガ`).

Scan cost is bounded by size caps (4 Mi characters per blob, 1024 terms of at most 256 characters), not by a latency promise. On a development machine, plain 4 Mi-character inputs scan in about 0.5–1.5 s, while adversarial 4 Mi-character inputs dense with invisible characters or pre-mark invisible runs can take several seconds each (roughly 2–9 s depending on the input and machine load).

Known limits, left to independent review rather than the automated scan: cross-script homoglyphs (for example Cyrillic `а` standing in for Latin `a`) and encoded forms such as HTML entities or URL percent-encoding are not decoded or folded.
