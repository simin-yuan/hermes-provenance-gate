"""Role and condition are recorded on the evidence row; the verdict does not read them yet.

The corpus is the four adversarial cases from u/jylusdev (plus variants), run through the real
gate path. ``known-hole`` rows are expected to fail: they are the reason the fields exist, and
they turn into a failure the day the verdict learns to read them, so this file gets updated
instead of quietly passing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from provenance_gate import attribution, store
from provenance_gate.hooks import Gate

CORPUS = Path(__file__).parent / "corpus" / "attribution.jsonl"


def load():
    with open(CORPUS, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


CASES = load()


@pytest.fixture(autouse=True)
def default_settings(monkeypatch):
    for name in ("PROVENANCE_GATE_MODE", "PROVENANCE_GATE_SCOPE", "PROVENANCE_GATE_RETENTION_DAYS",
                 "PROVENANCE_GATE_SEARCH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PROVENANCE_GATE_MODE", "block")


# --- the two recorded fields ---------------------------------------------------------------------

@pytest.mark.parametrize("quote, expected", [
    ("I decided we ship on Friday", attribution.ROLE_SELF),
    ("we're using Postgres now", attribution.ROLE_SELF),
    ("let's go with Postgres", attribution.ROLE_SELF),
    ("Alice decided we are using Postgres", attribution.ROLE_RELAYED),
    ("Alice said the migration is approved", attribution.ROLE_RELAYED),
    ("my manager suggested dropping the nightly job", attribution.ROLE_RELAYED),
    ("Our lead approved the migration", attribution.ROLE_RELAYED),
    ("同事说下周一上线", attribution.ROLE_RELAYED),
    ("他建议改用 Postgres", attribution.ROLE_RELAYED),
    ("我决定周五上线", attribution.ROLE_SELF),
    # Ambiguous: a third-party report plus the user's own agreement. Guessing which half is the
    # decision is the expensive mistake, so it stays unknown rather than being forced to a side.
    ("Alice suggested X and I agree", attribution.ROLE_UNKNOWN),
    ("my manager suggested dropping the nightly job and I agree", attribution.ROLE_UNKNOWN),
    # The gate's own subject is not reported speech.
    ("The user decided to ship on Friday", attribution.ROLE_UNKNOWN),
    ("Postgres", attribution.ROLE_UNKNOWN),
    ("", attribution.ROLE_UNKNOWN),
    (None, attribution.ROLE_UNKNOWN),
])
def test_role_of(quote, expected):
    assert attribution.role_of(quote) == expected


@pytest.mark.parametrize("quote, expected", [
    ("Let's choose Postgres only if the benchmark passes.", attribution.CONDITION_UNRESOLVED),
    ("We go with Postgres unless the benchmark fails.", attribution.CONDITION_UNRESOLVED),
    ("We ship Postgres as long as legal signs off.", attribution.CONDITION_UNRESOLVED),
    ("If the benchmark passes next week, we switch to Postgres.", attribution.CONDITION_UNRESOLVED),
    ("我们只要测试过了就上线", attribution.CONDITION_UNRESOLVED),
    ("I decided we ship on Friday", attribution.CONDITION_NONE),
    ("Postgres", attribution.CONDITION_NONE),
    ("", attribution.CONDITION_UNKNOWN),
    (None, attribution.CONDITION_UNKNOWN),
])
def test_condition_of(quote, expected):
    assert attribution.condition_of(quote) == expected


def test_fields_land_on_the_evidence_row(tmp_path):
    gate = Gate(directory=lambda: tmp_path)
    gate.pre_llm_call(session_id="s1",
                      user_message="Alice decided we're using Postgres for the new service.",
                      sender_id="u-1")
    gate.pre_tool_call(tool_name="memory",
                       args={"action": "add", "target": "memory",
                             "content": 'User decided to use Postgres ("Alice decided we\'re using '
                                        'Postgres for the new service.")'},
                       session_id="s1", turn_id="t1")
    [row] = store.read_rows(tmp_path / store.EVIDENCE_FILE)
    assert (row["role"], row["condition"]) == (attribution.ROLE_RELAYED, attribution.CONDITION_NONE)


def test_condition_is_recorded_even_though_the_verdict_ignores_it(tmp_path):
    gate = Gate(directory=lambda: tmp_path)
    gate.pre_llm_call(session_id="s1",
                      user_message="We ship Postgres as long as legal signs off.", sender_id="u-1")
    gate.pre_tool_call(tool_name="memory",
                       args={"action": "add", "target": "memory",
                             "content": 'User decided to ship Postgres ("We ship Postgres as long '
                                        'as legal signs off.")'},
                       session_id="s1", turn_id="t1")
    [row] = store.read_rows(tmp_path / store.EVIDENCE_FILE)
    assert row["condition"] == attribution.CONDITION_UNRESOLVED
    assert row["verdict"] == "pass"  # recorded, not judged: the block comes in a later version


# --- the four cases, end to end ------------------------------------------------------------------

@pytest.mark.parametrize("row", CASES, ids=[c["case"] for c in CASES])
def test_attribution_case(row, request, tmp_path):
    if row["status"] == "known-hole":
        request.node.add_marker(pytest.mark.xfail(
            strict=True,
            reason="known hole: the quote verifies, so role/condition never reaches the verdict"),
        )
    gate = Gate(directory=lambda: tmp_path)
    gate.pre_llm_call(session_id="s1", user_message=row["said"], sender_id="u-1")
    result = gate.pre_tool_call(tool_name="memory",
                                args={"action": "add", "target": "memory", "content": row["write"]},
                                session_id="s1", turn_id="t1")
    rows = store.read_rows(tmp_path / store.EVIDENCE_FILE)
    blocked = result is not None
    assert blocked == (row["expect"] == "blocked"), (row["case"], rows)
    if row["reason"]:
        assert rows[0]["reason"] == row["reason"]
