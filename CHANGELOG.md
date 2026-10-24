# Changelog

## 0.1.3 — 2026-10-25

- Added the timing test for `_ZH_BRACKETED` (`tests/test_performance.py::test_zh_brackets_stay_linear`):
  three shapes made only of opening brackets (`「`, `（`, `“`), each measured in its own process at
  40,000 and 160,000 characters with a hard 60 s timeout. The test fails when 4x the data costs more
  than 8x the time, or when 160,000 characters cost more than 0.5 s. On the 0.1.1 pattern it fails:
  160,000 `「` took 11.7 s.
- No pattern changed in this release. All 46 patterns were measured again with the fixed adversarial
  shape set (10k and 40k characters, child process, hard timeout): none of them grows faster than
  the data.
- Measured for this release, same script and same machine: 10,000 characters 0.0020 s, 40,000
  characters 0.0080 s, a growth of x3.8 to x4.1 across the three shapes. The times in the 0.1.2
  section come from a different run; use these.
- Corpus unchanged: 213 legitimate lines are blocked 0 times, and all 174 claim cases give the same
  action, reason and evidence source as 0.1.2 (per-line diff is empty).

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
  - Measured after the fix: 40k characters cost well under 0.01 s (was 0.79 s). (The figure here
    and the one in the commit message come from a different measurement run; the 0.1.3 section has
    the numbers to use.)

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
