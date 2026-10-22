"""Settling a stored condition: append only, never rewrite the claim, never grade your own work."""

import json

import pytest

from provenance_gate import settlement, store


def evidence_row(claim="Ship the parser on Friday.", condition="unresolved", session="sess-a", **extra):
    row = {"ts": "2026-10-22T12:00:00+00:00", "session": session, "tool": "memory", "target": "memory",
           "claim": claim, "quote": "only if the benchmark passes", "msg_id": "m1", "matched_in": "session",
           "role": "stated", "condition": condition, "verdict": "pass"}
    row.update(extra)
    return row


@pytest.fixture()
def dirs(tmp_path):
    evidence = tmp_path / store.EVIDENCE_FILE
    store.append_rows(evidence, [evidence_row()])
    return evidence, tmp_path / settlement.SETTLEMENTS_FILE


def cid_of(path):
    return settlement.claim_id(store.read_rows(path)[0])


def test_settle_appends_and_leaves_the_claim_untouched(dirs):
    evidence, settlements = dirs
    before = evidence.read_bytes()

    row = settlement.settle(evidence, cid=cid_of(evidence), outcome=settlement.OUTCOME_SATISFIED,
                            by="Simon", by_kind=settlement.BY_USER, result="benchmark 41/41, run 1182")

    assert evidence.read_bytes() == before, "the evidence row must not be rewritten"
    stored = store.read_rows(settlements)
    assert len(stored) == 1 and stored[0]["kind"] == "settlement"
    assert stored[0]["claim_id"] == cid_of(evidence)
    assert stored[0]["claim"] == "Ship the parser on Friday."
    assert row["result"] == "benchmark 41/41, run 1182"


def test_pending_lists_open_claims_then_drops_them_once_settled(dirs):
    evidence, settlements = dirs
    assert [row["claim_id"] for row in settlement.pending(evidence, settlements)] == [cid_of(evidence)]

    settlement.settle(evidence, cid=cid_of(evidence), outcome=settlement.OUTCOME_FAILED, by="Simon",
                      by_kind=settlement.BY_USER, result="benchmark 12/41")
    assert settlement.pending(evidence, settlements) == []
    assert settlement.status(settlements, cid_of(evidence)) == settlement.OUTCOME_FAILED


def test_a_claim_with_no_open_condition_is_never_pending(dirs):
    evidence, settlements = dirs
    store.append_rows(evidence, [evidence_row(claim="Use Postgres.", condition="none")])
    pending = settlement.pending(evidence, settlements)
    assert [row["claim"] for row in pending] == ["Ship the parser on Friday."]


def test_the_writing_session_cannot_settle_its_own_claim(dirs):
    evidence, _ = dirs
    with pytest.raises(settlement.SettlementRefused, match="same session"):
        settlement.settle(evidence, cid=cid_of(evidence), outcome=settlement.OUTCOME_SATISFIED,
                          by="sess-a", by_kind=settlement.BY_USER, result="trust me")


def test_only_user_or_ci_may_settle(dirs):
    evidence, _ = dirs
    with pytest.raises(settlement.SettlementRefused, match="user or from CI"):
        settlement.settle(evidence, cid=cid_of(evidence), outcome=settlement.OUTCOME_SATISFIED,
                          by="hermes-agent", by_kind="agent", result="all green")


def test_a_settlement_needs_a_result_and_a_signer(dirs):
    evidence, _ = dirs
    cid = cid_of(evidence)
    with pytest.raises(settlement.SettlementRefused, match="name who settled it"):
        settlement.settle(evidence, cid=cid, outcome=settlement.OUTCOME_SATISFIED, by="",
                          by_kind=settlement.BY_USER, result="ok")
    with pytest.raises(settlement.SettlementRefused, match="justifies it"):
        settlement.settle(evidence, cid=cid, outcome=settlement.OUTCOME_SATISFIED, by="Simon",
                          by_kind=settlement.BY_USER, result="   ")


def test_unknown_claim_id_is_refused(dirs):
    evidence, _ = dirs
    with pytest.raises(settlement.SettlementRefused, match="no evidence row"):
        settlement.settle(evidence, cid="deadbeef1234", outcome=settlement.OUTCOME_SATISFIED,
                          by="Simon", by_kind=settlement.BY_USER, result="ok")


@pytest.mark.parametrize("bad", ["main", "HEAD", "abc", "zzzzzzz", "12345'"])
def test_a_revision_that_is_not_a_sha_is_refused_rather_than_dropped(dirs, bad):
    evidence, _ = dirs
    with pytest.raises(settlement.SettlementRefused, match="git commit sha"):
        settlement.settle(evidence, cid=cid_of(evidence), outcome=settlement.OUTCOME_SATISFIED,
                          by="ci:build", by_kind=settlement.BY_CI, result="41/41", revision=bad)


def test_a_proved_revision_is_kept_and_an_absent_one_is_simply_absent(dirs):
    evidence, settlements = dirs
    cid = cid_of(evidence)
    settlement.settle(evidence, cid=cid, outcome=settlement.OUTCOME_SATISFIED, by="ci:build",
                      by_kind=settlement.BY_CI, result="41/41", revision="1CA6622",
                      test="pytest -q")
    settlement.settle(evidence, cid=cid, outcome=settlement.OUTCOME_UNKNOWN, by="Simon",
                      by_kind=settlement.BY_USER, result="rerun pending")
    rows = store.read_rows(settlements)
    assert rows[0]["revision"] == "1ca6622" and rows[0]["test"] == "pytest -q"
    assert "revision" not in rows[1], "no revision is better than a guessed one"
    assert settlement.status(settlements, cid) == settlement.OUTCOME_UNKNOWN


def test_two_settlements_are_two_rows_and_the_newest_decides(dirs):
    evidence, settlements = dirs
    cid = cid_of(evidence)
    settlement.settle(evidence, cid=cid, outcome=settlement.OUTCOME_SATISFIED, by="ci:build",
                      by_kind=settlement.BY_CI, result="41/41")
    settlement.settle(evidence, cid=cid, outcome=settlement.OUTCOME_FAILED, by="Simon",
                      by_kind=settlement.BY_USER, result="that run was the wrong branch")
    assert len(store.read_rows(settlements)) == 2
    assert settlement.status(settlements, cid) == settlement.OUTCOME_FAILED


def test_the_same_claim_written_twice_has_one_id_and_one_pending_entry(dirs):
    evidence, settlements = dirs
    store.append_rows(evidence, [evidence_row()])
    rows = store.read_rows(evidence)
    assert len({settlement.claim_id(row) for row in rows}) == 1
    assert len(settlement.pending(evidence, settlements)) == 1


def test_claim_id_survives_a_prune_and_rewrite(tmp_path):
    path = tmp_path / store.EVIDENCE_FILE
    kept = evidence_row(claim="kept")
    store.append_rows(path, [evidence_row(claim="removed"), kept])
    before = settlement.claim_id(kept)
    store.rewrite_unlocked(path, [kept])
    assert settlement.claim_id(store.read_rows(path)[0]) == before


# --- through the command line --------------------------------------------------------------------

@pytest.fixture()
def run(tmp_path, capsys):
    import argparse

    from provenance_gate import cli

    parser = argparse.ArgumentParser(prog="hermes provenance-gate")
    cli.setup(parser)
    handler = cli.make_handler(lambda: tmp_path)
    store.append_rows(tmp_path / store.EVIDENCE_FILE, [evidence_row()])

    def invoke(*argv):
        return handler(parser.parse_args(list(argv))), capsys.readouterr().out
    return invoke


def test_cli_pending_then_settle_then_pending_is_empty(run, tmp_path):
    code, out = run("pending")
    assert code == 0 and "Ship the parser on Friday." in out
    cid = store.read_rows(tmp_path / store.EVIDENCE_FILE)[0]
    cid = settlement.claim_id(cid)

    code, out = run("settle", cid, "--outcome", "satisfied", "--by", "Simon", "--by-kind", "user",
                    "--result", "benchmark 41/41", "--revision", "1ca6622")
    assert code == 0
    stored = json.loads(out.strip())
    assert stored["outcome"] == "satisfied" and stored["revision"] == "1ca6622"

    code, out = run("pending")
    assert code == 0 and "no open claims" in out


def test_cli_settle_refuses_a_bad_revision_and_says_why(run):
    code, out = run("settle", "deadbeef1234", "--outcome", "satisfied", "--by", "Simon",
                    "--by-kind", "user", "--result", "ok")
    assert code == 2 and "no evidence row" in out
