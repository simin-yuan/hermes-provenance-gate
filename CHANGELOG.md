# Changelog

## 0.1.2 — 2026-10-25

- Fixed a third super-linear pattern in `claims.py`, found by re-auditing every regex in this plugin
  against a fixed adversarial shape set (10k → 40k characters, hard timeout, child process):
  - `_ZH_BRACKETED`: each alternative was `<opener>[^<closer>]*<closer>` with an unbounded middle run.
    On input made only of openers (`「` × 40k) every start position scanned to the end before failing:
    ×15.6 from 10k to 40k, 0.79 s at 40k. The same pattern is present unchanged in 0.1.0 and 0.1.1 —
    this is not a regression introduced by 0.1.1.
  - The middle runs are now bounded to 200 characters. The only caller passes a ≤120-character slice
    (`_WINDOW`), so detection results are unchanged for every input that can actually reach it;
    the worst case drops from quadratic to linear. Output equivalence was checked by differential
    testing against the 0.1.1 pattern on 200,000 random strings.
  - Measured after the fix: 40k characters cost 0.006 s (was 0.79 s), ×1.0 across the 10k → 40k step.

## 0.1.1 — 2026-10-23

- Fixed two super-linear patterns in `claims.py`, found in a self-audit after the same class was
  reported against a sibling plugin (NousResearch/hermes-agent#142381). Both had two adjacent
  unbounded whitespace runs that were retried from every backtrack position:
  - `_KEY_VALUE`: `"key"` followed by 1 MiB of spaces cost 0.67 s before reaching the separator.
  - `_TRAILING_PAREN`: `"a"` + 1 MiB of spaces + `"b"` — a run of whitespace with no parenthesis in
    it — did not finish in 30 s, because `sub()` retried the leading `\s*` at every offset.
  Both now take under 5 ms on the same input. The rewrites use possessive quantifiers, so this
  release needs Python 3.11+ (Hermes itself runs on 3.14).
- Added those shapes to `tests/test_performance.py`, each in its own process with a 20 s timeout so a
  regression fails the suite instead of wedging it, and three whitespace entries to the end-to-end
  adversarial set.
- No change to what is or is not flagged: `_KEY_VALUE` still takes a key of at most 60 characters
  that may contain spaces but no longer captures trailing whitespace into the key (the caller strips
  it), and `_TRAILING_PAREN` relies on the caller's `rstrip()` for the whitespace it used to absorb.

Earlier versions: see `git log`.
