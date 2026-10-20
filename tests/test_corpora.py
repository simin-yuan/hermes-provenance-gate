"""Corpora: legitimate memory/skill text must never be blocked; every claim case must get its
expected outcome. Run with ``-s`` to see the counts and pass rates."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from provenance_gate import capture, store
from provenance_gate.hooks import Gate

CORPUS = Path(__file__).parent / "corpus"


def load(name):
    with open(CORPUS / name, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


LEGIT = load("legit.jsonl")
CLAIMS = load("claims.jsonl")


@pytest.fixture(autouse=True)
def default_settings(monkeypatch):
    for name in ("PROVENANCE_GATE_MODE", "PROVENANCE_GATE_SCOPE", "PROVENANCE_GATE_RETENTION_DAYS",
                 "PROVENANCE_GATE_SEARCH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PROVENANCE_GATE_MODE", "block")


def test_corpus_sizes():
    assert len(LEGIT) >= 150
    assert len(CLAIMS) >= 80
    assert {"en", "zh"} <= {("zh" if any("一" <= ch <= "鿿" for ch in r["text"]) else "en") for r in LEGIT}


def legit_blocks(tmp_path) -> list[str]:
    gate = Gate(directory=lambda: tmp_path)
    blocked = []
    for row in LEGIT:
        calls = [("memory", {"action": "add", "target": "memory", "content": row["text"]}),
                 ("skill_manage", {"operations": [{"action": "create", "name": "notes", "content": row["text"]}]})]
        for tool, args in calls:
            if gate.pre_tool_call(tool_name=tool, args=args, session_id="legit", turn_id="t") is not None:
                blocked.append(f"{tool}: {row['text']}")
    return blocked


def test_legit_corpus_has_zero_blocks(tmp_path, capsys):
    blocked = legit_blocks(tmp_path)
    with capsys.disabled():
        print(f"\nlegit corpus: {len(LEGIT)} lines x 2 tools, blocked {len(blocked)}, "
              f"pass rate {100 * (2 * len(LEGIT) - len(blocked)) / (2 * len(LEGIT)):.1f}%")
    assert blocked == []
    assert store.read_rows(tmp_path / store.EVIDENCE_FILE) == []  # no claims, so no evidence rows


def run_case(case, directory: Path):
    gate = Gate(directory=lambda: directory)
    for said in case["said"]:
        capture.capture(directory, said["session"], said["text"])
    previous = os.environ.get("PROVENANCE_GATE_SEARCH")
    os.environ["PROVENANCE_GATE_SEARCH"] = case.get("search", "session")
    try:
        result = gate.pre_tool_call(tool_name=case["tool"], args=case["args"], session_id=case["session"],
                                    turn_id="t")
    finally:
        if previous is None:
            os.environ.pop("PROVENANCE_GATE_SEARCH", None)
        else:
            os.environ["PROVENANCE_GATE_SEARCH"] = previous
    rows = store.read_rows(directory / store.EVIDENCE_FILE)
    return result, rows


def case_problems(case, directory: Path) -> list[str]:
    result, rows = run_case(case, directory)
    problems = []
    if case["expect"] == "pass-no-evidence":
        if result is not None:
            problems.append(f"blocked: {result['message']}")
        if rows:
            problems.append(f"evidence rows {rows}")
    elif case["expect"] == "pass":
        if result is not None:
            problems.append(f"blocked: {result['message']}")
        if not rows or any(r["verdict"] != "pass" for r in rows):
            problems.append(f"evidence rows {rows}")
        elif any(r["matched_in"] != case["matched_in"] for r in rows):
            problems.append(f"matched_in {[r['matched_in'] for r in rows]} != {case['matched_in']}")
    else:
        if result is None or result.get("action") != "block":
            problems.append("not blocked")
        if not rows or rows[0]["verdict"] != "blocked" or rows[0]["reason"] != case["reason"]:
            problems.append(f"evidence rows {rows}")
    return problems


@pytest.mark.parametrize("case", CLAIMS, ids=[c["id"] for c in CLAIMS])
def test_claim_case(case, tmp_path):
    assert case_problems(case, tmp_path) == []


def test_claims_corpus_summary(tmp_path, capsys):
    met = 0
    by_category: dict[str, list[int]] = {}
    for index, case in enumerate(CLAIMS):
        directory = tmp_path / str(index)
        directory.mkdir()
        ok = not case_problems(case, directory)
        met += ok
        by_category.setdefault(case["category"], [0, 0])
        by_category[case["category"]][0] += ok
        by_category[case["category"]][1] += 1
    with capsys.disabled():
        print(f"\nclaims corpus: {len(CLAIMS)} cases, expected outcome met {met}, "
              f"pass rate {100 * met / len(CLAIMS):.1f}%")
        for category, (ok, total) in sorted(by_category.items()):
            print(f"  {category}: {ok}/{total}")
    assert met == len(CLAIMS)


REGRESSIONS = load("audit_regressions.jsonl")


@pytest.mark.parametrize("row", REGRESSIONS, ids=[f"r{i:03d}" for i in range(len(REGRESSIONS))])
def test_audit_regression(row):
    """Shapes reported by the independent audits, labelled by hand (claim or not)."""
    from provenance_gate.claims import find_claims
    assert bool(find_claims(row["text"])) == row["claim"], row["text"]


def test_audit_regressions_summary():
    assert len(REGRESSIONS) == 100 and sum(r["claim"] for r in REGRESSIONS) == 40


BLIND = load("blind.jsonl")
BLIND_SHA256 = "2093317c9b20c55791f907f18fdaadd7904fe61fbbaac8871db59eaab7654274"


def test_blind_set_is_unchanged():
    """Written and labelled by an independent auditor before reading the code. Five lines were
    reworded before publication to remove names and details of the author's own setup (labels
    unchanged); nothing else has been edited."""
    import hashlib
    data = (CORPUS / "blind.jsonl").read_bytes()
    assert hashlib.sha256(data).hexdigest() == BLIND_SHA256


def test_blind_set_accuracy():
    """The numbers published in the README (Measured accuracy). Update both together."""
    from provenance_gate.claims import find_claims
    tp = sum(1 for r in BLIND if r["claim"] and find_claims(r["text"]))
    fp = sum(1 for r in BLIND if not r["claim"] and find_claims(r["text"]))
    claims = sum(1 for r in BLIND if r["claim"])
    assert (len(BLIND), claims) == (133, 49)
    assert fp == 0 and tp >= 34
