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
