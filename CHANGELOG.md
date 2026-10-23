# Changelog

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
