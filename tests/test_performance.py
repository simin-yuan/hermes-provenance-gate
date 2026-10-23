"""Checking stays linear: these budgets catch a quadratic blow-up (minutes), not small speed
differences between machines. Shared CI runners, Windows ones especially, are several times slower
than a developer machine, so the budgets are generous; the README reports locally measured times."""

from __future__ import annotations

import time

import pytest

from provenance_gate import verdict

MIB = 1024 * 1024
BUDGET_SECONDS = 8.0


def _fit(unit: str) -> str:
    return (unit * (MIB // len(unit) + 1))[:MIB]


ADVERSARIAL = {
    "conditional claims": _fit("if the user decided "),
    "quote marks": _fit('"'),
    "inch marks and claims": _fit('27" the user approved it 6" '),
    "single quotes": _fit("'a 'b c' "),
    "apostrophes": _fit("'a'a'a "),
    "one huge sentence": "The user decided " + _fit("x ") + ' ("I decided we ship on Friday")',
    "tiny sentences": _fit("a. "),
    "chinese conditionals": _fit("如果用户同意，"),
    "many claims": _fit("The user decided item. "),
    "corner brackets": _fit("「"),
    "mixed quotes": _fit("“‘「『\"'"),
    "spaces": _fit(" "),
    "key then spaces": "key" + _fit(" "),
    "sentence then spaces": "The user decided it." + _fit(" "),
}


@pytest.mark.parametrize("name", list(ADVERSARIAL))
def test_one_mib_is_checked_quickly(name):
    text = ADVERSARIAL[name]
    said = [{"ts": "2099-01-01T00:00:00Z", "session_id": "s", "msg_id": "m", "text": "I decided we ship on Friday"}]
    start = time.perf_counter()
    verdict.evaluate("memory", {"action": "add", "target": "memory", "content": text}, "s", said)
    elapsed = time.perf_counter() - start
    assert elapsed < BUDGET_SECONDS, f"{name}: {elapsed:.2f}s"


def test_large_store_profile_search_is_quick(tmp_path, monkeypatch):
    """A ~15 MB said.jsonl, profile search, 100 claims each with a quote: under the budget after the
    first (parsing) read."""
    from provenance_gate import capture, store
    monkeypatch.setenv("PROVENANCE_GATE_SEARCH", "profile")
    path = tmp_path / store.SAID_FILE
    filler = "lorem ipsum dolor sit amet " * 20
    rows = [{"ts": "2099-01-01T00:00:00Z", "session_id": f"s{i % 50}", "msg_id": f"m{i}",
             "text": f"{filler} message {i}"} for i in range(26000)]
    rows.append({"ts": "2099-01-01T00:00:00Z", "session_id": "s1", "msg_id": "mx", "text": "I decided we ship on Friday"})
    store.append_rows(path, rows)
    assert path.stat().st_size > 14_000_000
    capture.said_rows(tmp_path)  # first read parses and caches
    text = "\n".join(f'User decided to ship on Friday ("I decided we ship on Friday {k}").' for k in range(100))
    start = time.perf_counter()
    said = capture.said_rows(tmp_path)
    verdict.evaluate("memory", {"action": "add", "target": "memory", "content": text}, "s1", said,
                     search=verdict.SEARCH_PROFILE)
    assert time.perf_counter() - start < BUDGET_SECONDS * 2


WORKER = """
import sys, time
sys.path.insert(0, {root!r})
from provenance_gate import claims
text = {expr}
start = time.perf_counter()
claims.is_question(text)
claims._key_value_exempt(text, 0)
print(f"{{time.perf_counter() - start:.4f}}")
"""

WHITESPACE_RUNS = {
    # A run of spaces and no separator: _KEY_VALUE tried a second, unbounded whitespace run from every
    # backtrack position of the key, and _TRAILING_PAREN retried its leading whitespace at every offset of
    # a sub() scan, so a 1 MiB run with no parenthesis in it never terminated. Measured before the fix:
    # "key" + 1 MiB of spaces cost 0.67 s and "a" + 1 MiB + "b" did not finish in 30 s.
    "spaces": "' ' * (1 << 20)",
    "key then spaces": "'key' + ' ' * (1 << 20)",
    "key then spaces then colon": "'key' + ' ' * (1 << 20) + ': value'",
    "spaces between words": "'a' + ' ' * (1 << 20) + 'b'",
    "sentence then spaces": "'The user decided it.' + ' ' * (1 << 20) + 'Done'",
}


@pytest.mark.parametrize("name", list(WHITESPACE_RUNS))
def test_whitespace_runs_do_not_hang(name):
    """Run each case in its own process: a regression must fail the suite, not wedge it, because re
    cannot be interrupted once it starts."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    script = WORKER.format(root=str(root), expr=WHITESPACE_RUNS[name])
    try:
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                              timeout=20, cwd=str(root))
    except subprocess.TimeoutExpired:
        pytest.fail(f"{name}: still running after 20 s — the pattern is super-linear again")
    assert proc.returncode == 0, proc.stderr.strip()[-1500:]
    elapsed = float(proc.stdout.strip().splitlines()[-1])
    assert elapsed < 1.0, f"{name}: {elapsed:.3f}s"
