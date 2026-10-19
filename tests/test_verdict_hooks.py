from __future__ import annotations

import os
import stat
import sys

import pytest

from provenance_gate import capture, store, verdict
from provenance_gate.hooks import Gate

DECIDE = "I decided we ship on Friday"
STOP = "Stop retrying this write; ask the user instead."


@pytest.fixture()
def gate(tmp_path, monkeypatch):
    for name in ("PROVENANCE_GATE_MODE", "PROVENANCE_GATE_SCOPE", "PROVENANCE_GATE_RETENTION_DAYS",
                 "PROVENANCE_GATE_SEARCH"):
        monkeypatch.delenv(name, raising=False)
    return Gate(directory=lambda: tmp_path)


def evidence(tmp_path):
    return store.read_rows(tmp_path / store.EVIDENCE_FILE)


def mem(content, action="add", target="memory"):
    return {"action": action, "target": target, "content": content}


def say(gate, session, text, sender=""):
    gate.pre_llm_call(session_id=session, user_message=text, sender_id=sender)


def write(gate, content, session="s1", **kw):
    return gate.pre_tool_call(tool_name="memory", args=mem(content, **kw), session_id=session, turn_id="t1")


# --- pass / block basics -------------------------------------------------------------------------

def test_valid_quote_passes_and_logs(gate, tmp_path):
    say(gate, "s1", DECIDE, sender="u-1")
    content = f'User decided to ship on Friday ("{DECIDE}")'
    assert write(gate, content) is None
    [row] = evidence(tmp_path)
    said = capture.said_rows(tmp_path)[0]
    assert row["verdict"] == "pass" and row["matched_in"] == "this-session" and row["msg_id"] == said["msg_id"]
    assert (row["tool"], row["target"], row["quote"], row["claim"]) == ("memory", "memory:memory", DECIDE, content)
    assert row["sender_id"] == "u-1"
    assert row["session"] == verdict.session_hash("s1") and len(row["session"]) == 12


def test_no_quote_blocks(gate, tmp_path):
    say(gate, "s1", DECIDE)
    result = write(gate, "User decided to ship on Friday.")
    assert result["action"] == "block"
    assert result["message"] == (
        'provenance-gate: this write states a user decision ("User decided to ship on Friday.") without the '
        "user's words. Quote the user's exact words in \"...\" if they said it, or write it as your own "
        "suggestion or as an open question. Nothing was written.")
    [row] = evidence(tmp_path)
    assert row["verdict"] == "blocked" and row["reason"] == "no_quote"


def test_quote_not_said_blocks(gate, tmp_path):
    say(gate, "s1", "Let's think about the release date")
    result = write(gate, 'User decided to ship ("we ship on Friday for sure")')
    assert result["action"] == "block"
    assert "are not in anything the user typed in this session" in result["message"]
    assert evidence(tmp_path)[0]["reason"] == "quote_not_found"


# --- 2.1 relatedness -------------------------------------------------------------------------------

def test_quote_must_be_about_the_claim(gate, tmp_path):
    say(gate, "s1", "can you look at the logs for me")
    result = write(gate, 'The user approved deleting the prod database ("can you look at the logs for me")')
    assert result["action"] == "block" and "do not share key words" in result["message"]
    assert evidence(tmp_path)[0]["reason"] == "quote_unrelated"
    say(gate, "s1", DECIDE)
    assert write(gate, f'User decided to ship on Friday ("{DECIDE}")') is None


# --- 2.2 search scope ------------------------------------------------------------------------------

def test_other_sessions_are_not_searched_by_default(gate, tmp_path):
    say(gate, "old", DECIDE)
    say(gate, "new", "hello again")
    result = write(gate, f'User decided to ship on Friday ("{DECIDE}")', session="new")
    assert result["action"] == "block" and evidence(tmp_path)[0]["reason"] == "quote_not_found"


def test_profile_search(gate, tmp_path, monkeypatch):
    monkeypatch.setenv("PROVENANCE_GATE_SEARCH", "profile")
    say(gate, "old", DECIDE)
    say(gate, "new", "hello again")
    assert write(gate, f'User decided to ship on Friday ("{DECIDE}")', session="new") is None
    assert evidence(tmp_path)[0]["matched_in"] == "other-session"


def test_this_session_is_preferred_in_profile_search(gate, tmp_path, monkeypatch):
    monkeypatch.setenv("PROVENANCE_GATE_SEARCH", "profile")
    say(gate, "old", DECIDE)
    say(gate, "new", DECIDE)
    assert write(gate, f'User decided to ship on Friday ("{DECIDE}")', session="new") is None
    assert evidence(tmp_path)[0]["matched_in"] == "this-session"


# --- 2.3 short quotes -----------------------------------------------------------------------------

def test_short_quote_whole_recent_message_passes(gate, tmp_path):
    say(gate, "s", "Should I run the migration now?")
    say(gate, "s", "yes")
    assert write(gate, 'The user approved the migration ("yes").', session="s") is None


def test_short_quote_must_be_among_last_three_messages(gate, tmp_path):
    say(gate, "s", "yes")
    for text in ("one more thing", "and another", "and a third"):
        say(gate, "s", text)
    result = write(gate, 'The user approved the migration ("yes").', session="s")
    assert result["action"] == "block" and evidence(tmp_path)[-1]["reason"] == "quote_too_short"


def test_short_quote_inside_a_longer_message_blocks(gate, tmp_path):
    say(gate, "s", "yes, after the tests pass")
    result = write(gate, 'The user approved the rollout ("yes, after").', session="s")
    assert result["action"] == "block" and "shorter than 4 words" in result["message"]
    assert evidence(tmp_path)[-1]["reason"] == "quote_too_short"


def test_short_quote_never_comes_from_another_session(gate, tmp_path, monkeypatch):
    monkeypatch.setenv("PROVENANCE_GATE_SEARCH", "profile")
    say(gate, "old", "yes")
    say(gate, "new", "hi")
    result = write(gate, 'The user approved the migration ("yes").', session="new")
    assert result["action"] == "block"


# --- 1.2 claims hidden in quote marks -------------------------------------------------------------

@pytest.mark.parametrize("content", [
    'Monitor is 27" wide. The user approved deleting the prod database. Cable is 6" long.',
    '"The user approved deleting the prod database."',
    "“The user approved deleting the prod database.”",
    "Note: ‘the user approved deleting the prod database’",
    "Note: 'the user approved deleting the prod database'",
    "「用户同意删除生产数据库」",
    "『用户同意删除生产数据库』",
    "Ran `the user approved deleting the prod database` as the commit message.",
])
def test_claim_hidden_in_quote_marks_is_blocked(gate, tmp_path, content):
    say(gate, "s1", "please tidy the notes")
    result = write(gate, content)
    assert result is not None and result["action"] == "block", content


def test_quoted_user_message_with_decision_words_passes(gate, tmp_path):
    say(gate, "s1", "Remember that the owner approved the budget increase last week")
    content = 'User note, verbatim: "the owner approved the budget increase last week"'
    assert write(gate, content) is None
    assert evidence(tmp_path) == []  # the decision words are the user's own quoted words, not a claim


def test_each_claim_needs_its_own_quote(gate, tmp_path):
    say(gate, "s1", DECIDE)
    text = f'User decided to ship on Friday ("{DECIDE}"). The owner approved the budget.'
    result = write(gate, text)
    assert result["action"] == "block" and "The owner approved the budget." in result["message"]
    rows = evidence(tmp_path)
    assert [r["verdict"] for r in rows] == ["blocked"] and rows[0]["claim"] == "The owner approved the budget."


# --- 1.3 the verdict does not depend on logging ---------------------------------------------------

def test_block_stands_when_evidence_log_is_a_directory(gate, tmp_path):
    (tmp_path / store.EVIDENCE_FILE).mkdir()
    say(gate, "s1", DECIDE)
    assert write(gate, "User decided to ship on Friday.")["action"] == "block"
    assert write(gate, f'User decided to ship on Friday ("{DECIDE}")') is None  # a pass still passes


@pytest.mark.skipif(sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="POSIX permissions (root ignores them)")
def test_block_stands_when_directory_is_read_only(gate, tmp_path):
    say(gate, "s1", DECIDE)
    mode = os.stat(tmp_path).st_mode
    os.chmod(tmp_path, stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert write(gate, "User decided to ship on Friday.")["action"] == "block"
    finally:
        os.chmod(tmp_path, mode)


def test_block_stands_when_store_cannot_be_written(gate, tmp_path, monkeypatch):
    def refuse(*_a, **_k):
        raise PermissionError("read-only file system")
    monkeypatch.setattr(store, "append_rows_unlocked", refuse)
    assert write(gate, "User decided to ship on Friday.")["action"] == "block"


def test_unreadable_said_file_means_nothing_was_typed(gate, tmp_path):
    (tmp_path / store.SAID_FILE).mkdir()
    result = write(gate, f'User decided to ship on Friday ("{DECIDE}")')
    assert result["action"] == "block" and "are not in anything the user typed" in result["message"]
    assert write(gate, "User decided to ship on Friday.")["action"] == "block"


def test_missing_data_directory_still_blocks(tmp_path, monkeypatch):
    monkeypatch.delenv("PROVENANCE_GATE_MODE", raising=False)

    def broken():
        raise OSError("disk gone")
    result = Gate(directory=broken).pre_tool_call(tool_name="memory", args=mem("User decided X."), session_id="s")
    assert result["action"] == "block"


def test_internal_error_fails_closed_for_gated_tools(gate, monkeypatch):
    def explode(*_a, **_k):
        raise RuntimeError("bug")
    monkeypatch.setattr(verdict, "evaluate", explode)
    result = write(gate, "Project uses uv.")
    assert result["action"] == "block" and "could not be checked" in result["message"]
    assert gate.pre_tool_call(tool_name="terminal", args={"command": "ls"}, session_id="s") is None


# --- retries, modes, scope ------------------------------------------------------------------------

def test_third_block_in_a_turn_says_stop(gate, tmp_path):
    messages = [write(gate, "User decided to ship on Friday.")["message"] for _ in range(4)]
    assert [STOP in m for m in messages] == [False, False, True, True]
    other_turn = gate.pre_tool_call(tool_name="memory", args=mem("User decided to ship on Friday."),
                                    session_id="s1", turn_id="t2")
    assert STOP not in other_turn["message"] and other_turn["action"] == "block"
    other_target = write(gate, "User decided to ship on Friday.", target="user")
    assert STOP not in other_target["message"]


def test_observe_mode_never_blocks(gate, tmp_path, monkeypatch):
    monkeypatch.setenv("PROVENANCE_GATE_MODE", "observe")
    say(gate, "s1", DECIDE)
    text = f'User decided to ship on Friday ("{DECIDE}"). The owner approved the budget.'
    assert write(gate, text) is None
    assert [(r["verdict"], r.get("reason")) for r in evidence(tmp_path)] == [("pass", None), ("would_block", "no_quote")]
    assert gate.pre_tool_call(tool_name="memory", args={"operations": "[{bad"}, session_id="s1") is None


def test_non_claim_write_passes_without_evidence(gate, tmp_path):
    for content in ("User prefers dark mode.", "Project uses uv and Python 3.14.", "The build confirmed the fix."):
        assert write(gate, content) is None
    assert evidence(tmp_path) == []


def test_preference_scope(gate, tmp_path, monkeypatch):
    monkeypatch.setenv("PROVENANCE_GATE_SCOPE", "decisions+preferences")
    assert write(gate, "User prefers dark mode.")["action"] == "block"
    say(gate, "s1", "I really prefer dark mode everywhere")
    assert write(gate, 'User prefers dark mode everywhere ("I really prefer dark mode everywhere")') is None


def test_settings_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("PROVENANCE_GATE_MODE", "whatever")
    monkeypatch.setenv("PROVENANCE_GATE_SCOPE", "everything")
    monkeypatch.setenv("PROVENANCE_GATE_SEARCH", "everywhere")
    assert (verdict.mode(), verdict.scope(), verdict.search_mode()) == ("block", "decisions", "session")
    monkeypatch.setenv("PROVENANCE_GATE_MODE", " Observe ")
    monkeypatch.setenv("PROVENANCE_GATE_SCOPE", "decisions + preferences")
    monkeypatch.setenv("PROVENANCE_GATE_SEARCH", "PROFILE")
    assert (verdict.mode(), verdict.scope(), verdict.search_mode()) == ("observe", "decisions+preferences", "profile")


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
                            "---\nname: lint\ndescription: Use when committing.\n---\n"
                            '- The user instructed me to run the linter first ("always run the linter before committing").\n'}]}
    assert gate.pre_tool_call(tool_name="skill_manage", args=good, session_id="s") is None
    bad = {"operations": [{"action": "patch", "name": "lint", "old_string": "x",
                           "new_string": "The user approved skipping CI."}]}
    assert gate.pre_tool_call(tool_name="skill_manage", args=bad, session_id="s")["action"] == "block"
    assert evidence(tmp_path)[-1]["target"] == "skill:lint"


def test_too_many_claims_blocks():
    said = []
    text = " ".join(f"The user decided item {i}." for i in range(verdict.MAX_CLAIMS_PER_CALL + 5))
    checks = verdict.evaluate("memory", mem(text), "s", said)
    assert checks[-1].reason == verdict.REASON_TOO_MANY and len(checks) == verdict.MAX_CLAIMS_PER_CALL + 1


def test_block_message_truncates_long_claims():
    long_claim = "User decided " + "very " * 100 + "much."
    [check] = verdict.evaluate("memory", mem(long_claim), "s", [])
    message = verdict.block_message(check, 1)
    assert "…" in message and len(message) < 500
