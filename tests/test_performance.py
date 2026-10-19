"""Checking is linear: 1 MiB of adversarial text is checked well within the time budget."""

from __future__ import annotations

import time

import pytest

from provenance_gate import verdict

MIB = 1024 * 1024
BUDGET_SECONDS = 2.0


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
