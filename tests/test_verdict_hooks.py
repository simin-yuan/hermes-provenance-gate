from __future__ import annotations

import pytest

from provenance_gate import capture, store, verdict
from provenance_gate.hooks import Gate


@pytest.fixture()
def gate(tmp_path, monkeypatch):
    for name in ("PROVENANCE_GATE_MODE", "PROVENANCE_GATE_SCOPE", "PROVENANCE_GATE_RETENTION_DAYS"):
        monkeypatch.delenv(name, raising=False)
    return Gate(directory=lambda: tmp_path)


def evidence(tmp_path):
    return store.read_rows(tmp_path / store.EVIDENCE_FILE)


def mem(content, action="add", target="memory"):
    return {"action": action, "target": target, "content": content}


# --- what a call adds --------------------------------------------------------------------------

def test_added_texts_memory_shapes():
    assert verdict.added_texts("memory", mem("a")) == [("memory:memory", "a")]
    assert verdict.added_texts("memory", mem("b", "patch", "user") | {"old_text": "x"}) == [("memory:user", "b")]
    assert verdict.added_texts("memory", {"action": "patch", "target": "user", "new_text": "c", "old_text": "x"}) == [
        ("memory:user", "c")]
    assert verdict.added_texts("memory", {"action": "remove", "target": "memory", "old_text": "x"}) == []
    batch = {"target": "memory", "operations": [
        {"action": "add", "content": "one"}, {"action": "remove", "old_text": "two"},
        {"action": "patch", "old_text": "t", "new_text": "three"}]}
    assert verdict.added_texts("memory", batch) == [("memory:memory", "one"), ("memory:memory", "three")]


def test_added_texts_skill_shapes():
    ops = {"operations": [
        {"action": "create", "name": "a", "content": "C"},
        {"action": "patch", "name": "a", "old_string": "o", "new_string": "N"},
        {"action": "patch", "name": "b", "content": "FULL"},
        {"action": "write_file", "name": "a", "file_path": "references/x.md", "file_content": "F"},
        {"action": "remove_file", "name": "a", "file_path": "references/y.md"},
        {"action": "delete", "name": "c"},
    ]}
    assert verdict.added_texts("skill_manage", ops) == [
        ("skill:a", "C"), ("skill:a", "N"), ("skill:b", "FULL"), ("skill:a", "F")]
    legacy = {"action": "edit", "name": "d", "content": "E"}
    assert verdict.added_texts("skill_manage", legacy) == [("skill:d", "E")]
    legacy_patch = {"action": "patch", "name": "d", "old_string": "x", "new_string": "Y"}
    assert verdict.added_texts("skill_manage", legacy_patch) == [("skill:d", "Y")]
    assert verdict.added_texts("terminal", {"command": "echo"}) == []
    assert verdict.added_texts("memory", "not a dict") == []


# --- verdicts through the hook -------------------------------------------------------------------

def say(gate, session, text):
    gate.pre_llm_call(session_id=session, user_message=text)


def test_valid_quote_passes_and_logs(gate, tmp_path):
    say(gate, "s1", "I decided we ship on Friday")
    args = mem('User decided to ship on Friday ("I decided we ship on Friday")')
    assert gate.pre_tool_call(tool_name="memory", args=args, session_id="s1", turn_id="t1") is None
    [row] = evidence(tmp_path)
    said = capture.said_rows(tmp_path)[0]
    assert row["verdict"] == "pass" and row["matched_in"] == "this-session" and row["msg_id"] == said["msg_id"]
    assert row["tool"] == "memory" and row["target"] == "memory:memory"
    assert row["quote"] == "I decided we ship on Friday"
    assert row["claim"] == args["content"]
    assert row["session"] == verdict.session_hash("s1") and len(row["session"]) == 12


def test_no_quote_blocks(gate, tmp_path):
    say(gate, "s1", "I decided we ship on Friday")
    result = gate.pre_tool_call(tool_name="memory", args=mem("User decided to ship on Friday."), session_id="s1")
    assert result["action"] == "block"
    assert result["message"] == (
        'provenance-gate: this write states a user decision ("User decided to ship on Friday.") without the '
        "user's words. Quote the user's exact words in \"...\" if they said it, or write it as your own "
        "suggestion or as an open question. Nothing was written.")
    [row] = evidence(tmp_path)
    assert row["verdict"] == "blocked" and row["reason"] == "no_quote"


def test_quote_not_said_blocks(gate, tmp_path):
    say(gate, "s1", "Let's think about the release date")
    result = gate.pre_tool_call(tool_name="memory", args=mem('User decided to ship ("we ship on Friday for sure")'),
                                session_id="s1")
    assert result["action"] == "block" and "are not in anything the user said" in result["message"]
    assert evidence(tmp_path)[0]["reason"] == "quote_not_found"


def test_quote_from_other_session_passes(gate, tmp_path):
    say(gate, "old", "I decided we ship on Friday")
    say(gate, "new", "hello again")
    args = mem('User decided to ship on Friday ("I decided we ship on Friday")')
    assert gate.pre_tool_call(tool_name="memory", args=args, session_id="new") is None
    assert evidence(tmp_path)[0]["matched_in"] == "other-session"


def test_this_session_is_preferred(gate, tmp_path):
    say(gate, "old", "I decided we ship on Friday")
    say(gate, "new", "I decided we ship on Friday")
    args = mem('User decided to ship on Friday ("I decided we ship on Friday")')
    assert gate.pre_tool_call(tool_name="memory", args=args, session_id="new") is None
    assert evidence(tmp_path)[0]["matched_in"] == "this-session"


def test_short_quote_whole_message_passes_and_partial_blocks(gate, tmp_path):
    say(gate, "s", "yes")
    assert gate.pre_tool_call(tool_name="memory", args=mem('The user approved the migration ("yes").'),
                              session_id="s") is None
    say(gate, "s2", "yes, after the tests pass")
    result = gate.pre_tool_call(tool_name="memory", args=mem('The user approved the rollout ("yes, after").'),
                                session_id="s2")
    assert result["action"] == "block" and "shorter than 4 words" in result["message"]
    assert evidence(tmp_path)[-1]["reason"] == "quote_too_short"


def test_each_claim_needs_its_own_quote(gate, tmp_path):
    say(gate, "s", "I decided we ship on Friday")
    text = 'User decided to ship on Friday ("I decided we ship on Friday"). The owner approved the budget.'
    result = gate.pre_tool_call(tool_name="memory", args=mem(text), session_id="s")
    assert result["action"] == "block" and "The owner approved the budget." in result["message"]
    rows = evidence(tmp_path)
    assert [r["verdict"] for r in rows] == ["blocked"] and rows[0]["claim"] == "The owner approved the budget."


def test_third_block_in_a_turn_says_stop(gate, tmp_path):
    args = mem("User decided to ship on Friday.")
    messages = [gate.pre_tool_call(tool_name="memory", args=args, session_id="s", turn_id="t")["message"]
                for _ in range(4)]
    stop = "Stop retrying this write; ask the user instead."
    assert [stop in m for m in messages] == [False, False, True, True]
    other_turn = gate.pre_tool_call(tool_name="memory", args=args, session_id="s", turn_id="t2")
    assert stop not in other_turn["message"] and other_turn["action"] == "block"
    other_target = gate.pre_tool_call(tool_name="memory", args=mem(args["content"], target="user"),
                                      session_id="s", turn_id="t")
    assert stop not in other_target["message"]


def test_observe_mode_never_blocks(gate, tmp_path, monkeypatch):
    monkeypatch.setenv("PROVENANCE_GATE_MODE", "observe")
    say(gate, "s", "I decided we ship on Friday")
    text = 'User decided to ship on Friday ("I decided we ship on Friday"). The owner approved the budget.'
    assert gate.pre_tool_call(tool_name="memory", args=mem(text), session_id="s") is None
    assert [(r["verdict"], r.get("reason")) for r in evidence(tmp_path)] == [("pass", None), ("would_block", "no_quote")]


def test_non_claim_write_passes_without_evidence(gate, tmp_path):
    for content in ("User prefers dark mode.", "Project uses uv and Python 3.14.", "The build confirmed the fix."):
        assert gate.pre_tool_call(tool_name="memory", args=mem(content), session_id="s") is None
    assert evidence(tmp_path) == []


def test_preference_scope(gate, tmp_path, monkeypatch):
    monkeypatch.setenv("PROVENANCE_GATE_SCOPE", "decisions+preferences")
    result = gate.pre_tool_call(tool_name="memory", args=mem("User prefers dark mode."), session_id="s")
    assert result["action"] == "block"
    say(gate, "s", "I really prefer dark mode everywhere")
    assert gate.pre_tool_call(tool_name="memory", args=mem('User prefers dark mode ("I really prefer dark mode")'),
                              session_id="s") is None


def test_settings_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("PROVENANCE_GATE_MODE", "whatever")
    monkeypatch.setenv("PROVENANCE_GATE_SCOPE", "everything")
    assert verdict.mode() == "block" and verdict.scope() == "decisions"
    monkeypatch.setenv("PROVENANCE_GATE_MODE", " Observe ")
    monkeypatch.setenv("PROVENANCE_GATE_SCOPE", "decisions + preferences")
    assert verdict.mode() == "observe" and verdict.scope() == "decisions+preferences"


def test_removals_and_other_tools_are_ignored(gate, tmp_path):
    assert gate.pre_tool_call(tool_name="memory", args={"action": "remove", "target": "memory",
                                                        "old_text": "User decided to ship"}, session_id="s") is None
    assert gate.pre_tool_call(tool_name="write_file", args={"path": "MEMORY.md", "content": "User decided X."},
                              session_id="s") is None
    assert gate.pre_tool_call(tool_name="skill_manage", args={"operations": [{"action": "delete", "name": "x"}]},
                              session_id="s") is None
    assert evidence(tmp_path) == []


def test_skill_manage_claims(gate, tmp_path):
    say(gate, "s", "From now on always run the linter before committing")
    good = {"operations": [{"action": "create", "name": "lint", "content":
                            '---\nname: lint\ndescription: Use when committing.\n---\n'
                            '- The user instructed me to lint first ("always run the linter before committing").\n'}]}
    assert gate.pre_tool_call(tool_name="skill_manage", args=good, session_id="s") is None
    bad = {"operations": [{"action": "patch", "name": "lint", "old_string": "x",
                           "new_string": "The user approved skipping CI."}]}
    result = gate.pre_tool_call(tool_name="skill_manage", args=bad, session_id="s")
    assert result["action"] == "block"
    assert evidence(tmp_path)[-1]["target"] == "skill:lint"


def test_pre_tool_call_fails_open(tmp_path, monkeypatch):
    def broken():
        raise OSError("disk gone")
    assert Gate(directory=broken).pre_tool_call(tool_name="memory", args=mem("User decided X."), session_id="s") is None


def test_block_message_truncates_long_claims():
    long_claim = "User decided " + "very " * 100 + "much."
    check = verdict.ClaimCheck("memory:memory", verdict.claims_mod.find_claim(long_claim), False, "no_quote")
    message = verdict.block_message(check, 1)
    assert "…" in message and len(message) < 500
