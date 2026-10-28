# Changelog

## 0.1.7 — 2026-10-28

**Changed: both scripts look for `ots-cli.js` the same way — `OTS_JS`, else Node's module resolution.**

**Removed a hardcoded developer path** from `self_anchor.py` and `verify_self_anchor.js`. Lookup order
is now: `OTS_JS` (the package directory, or the `ots-cli.js` file itself), then `require.resolve` for
`javascript-opentimestamps`, then a stop that says to set `OTS_JS`. The package is still not vendored.

What this means for a user of 0.1.6: stamping on a machine without the package at that path stops
before writing anything, with an error naming a directory you cannot change. Upgrade, run
`npm install javascript-opentimestamps` where Node can see it or set `OTS_JS`, and stamping works
anywhere. Verifying on such a machine stops with one actionable line (exit 2) instead of a Node
module-not-found stack trace.

Also documented, because it bit this project: a scheduled upgrade loop must report when *every* proof
fails to verify. A loop that only speaks on confirmation cannot tell "still pending" from "the
verifier never opened the file", and both print the same silence.

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
