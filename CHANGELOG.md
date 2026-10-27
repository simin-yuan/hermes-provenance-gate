# Changelog

## 0.1.6 — 2026-10-28

**Fixed: `self_anchor` could overwrite the bytes an existing timestamp proof covers.**

The directory was derived from the date and the platform, so a second anchor to the same platform on the
same day landed in the directory the first one was using, and `body.txt` was copied over unconditionally.
The old `body.txt.ots` stayed where it was, so the next `ots stamp` printed a success line for a file
whose proof described the previous bytes. A timestamp that looks valid and proves nothing is worse than
no timestamp at all, and this tool's only product is that claim.

`--out` is now required and never inferred. The run refuses when the target directory already holds
different bytes, when a proof is there with no body beside it, or when `manifest.json` records a
different digest. Identical bytes are a no-op: the body and the original manifest are left alone, since a
second run is not a second anchoring.

This was not hypothetical — it happened while anchoring this repository's own convention document, and
the proof was recovered by hand from a copy. The check that would have stopped it is now the first thing
the tool does.

Tests: 58 passed, 1 skipped (`python -m pytest tests/test_verdict_hooks.py tests/test_cli.py
tests/test_self_anchor.py -q`), two of them for this fix.

## 0.1.5 — 2026-10-27

Docs-only: convention v0.1.1, which corrects a false statement about another plugin's `rule_key`.

## 0.1.4 — 2026-10-27

`ask` mode, with `rule_key` = `provenance-gate:<reason>`; convention v0.1 published alongside.
