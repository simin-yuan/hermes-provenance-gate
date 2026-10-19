"""End-to-end through a real Hermes install: real ``AIAgent(provider="custom")``, a fake
OpenAI-compatible server (streaming and non-streaming), the plugin installed in a temporary
``HERMES_HOME/plugins/provenance-gate`` and enabled via ``plugins.enabled``.

Skipped when Hermes is not importable, unless ``PROVENANCE_GATE_REQUIRE_E2E=1`` (CI sets it),
in which case a missing Hermes is a failure.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
WORKER = TESTS / "e2e_worker.py"
DECIDE = "I decided we ship on Friday"
STOP = "Stop retrying this write; ask the user instead."

if importlib.util.find_spec("run_agent") is None:
    if os.environ.get("PROVENANCE_GATE_REQUIRE_E2E") == "1":
        raise RuntimeError("PROVENANCE_GATE_REQUIRE_E2E=1 but Hermes (run_agent) is not importable")
    pytest.skip("Hermes is not importable; end-to-end tests skipped", allow_module_level=True)

pytestmark = pytest.mark.e2e
_cache: dict[tuple[str, bool], dict] = {}


def run(scenario: str, stream: bool, tmp_path_factory) -> dict:
    key = (scenario, stream)
    if key not in _cache:
        work = tmp_path_factory.mktemp(f"{scenario}-{'stream' if stream else 'plain'}")
        spec, out = work / "spec.json", work / "result.json"
        spec.write_text(json.dumps({"scenario": scenario, "stream": stream}), encoding="utf-8")
        proc = subprocess.run([sys.executable, str(WORKER), str(spec), str(out)], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=600)
        assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-4000:]
        _cache[key] = json.loads(out.read_text(encoding="utf-8"))
        assert _cache[key]["stream_flags"] == [stream]  # the requests really used this mode
    return _cache[key]


def tool_error(result_text: str) -> str:
    """Hermes hands a pre_tool_call block message to the model as ``{"error": <message>}``."""
    return json.loads(result_text)["error"]


def block_text(claim: str, reason_sentence: str = "") -> str:
    middle = f" {reason_sentence}" if reason_sentence else ""
    return (f'provenance-gate: this write states a user decision ("{claim}") without the user\'s words.{middle} '
            "Quote the user's exact words in \"...\" if they said it, or write it as your own suggestion "
            "or as an open question. Nothing was written.")


@pytest.fixture(params=[False, True], ids=["plain", "stream"])
def stream(request):
    return request.param


def test_1_quoted_decision_is_written(stream, tmp_path_factory):
    r = run("valid_quote", stream, tmp_path_factory)
    claim = f'User decided to ship on Friday ("{DECIDE}")'
    assert r["memory_md"].strip() == claim
    assert json.loads(r["tool_results"][0])["success"] is True
    [said] = r["said"]
    assert said["text"] == DECIDE and said["session_id"] == r["session_id"]
    [row] = r["evidence"]
    assert (row["verdict"], row["matched_in"], row["msg_id"], row["quote"]) == ("pass", "this-session", said["msg_id"], DECIDE)
    assert row["session"] == hashlib.sha256(r["session_id"].encode()).hexdigest()[:12]


def test_2_unquoted_decision_is_blocked(stream, tmp_path_factory):
    r = run("no_quote", stream, tmp_path_factory)
    assert r["memory_md"] == ""
    assert tool_error(r["tool_results"][0]) == block_text("User decided to ship on Friday.")
    [row] = r["evidence"]
    assert (row["verdict"], row["reason"]) == ("blocked", "no_quote")


def test_3_quote_the_user_never_said_is_blocked(stream, tmp_path_factory):
    r = run("quote_not_said", stream, tmp_path_factory)
    assert r["memory_md"] == ""
    fake = "we ship on Friday no matter what"
    assert tool_error(r["tool_results"][0]) == block_text(
        f'User decided to ship on Friday ("{fake}")', f'The quoted words ("{fake}") are not in anything the user said.')
    assert r["evidence"][0]["reason"] == "quote_not_found"


def test_3b_third_retry_in_a_turn_says_stop(stream, tmp_path_factory):
    r = run("retry_three_times", stream, tmp_path_factory)
    messages = [tool_error(t) for t in r["tool_results"]]
    assert [STOP in m for m in messages] == [False, False, True]
    assert r["memory_md"] == ""
    assert [row["verdict"] for row in r["evidence"]] == ["blocked"] * 3


def test_4_background_review_fork(stream, tmp_path_factory):
    r = run("background_review", stream, tmp_path_factory)
    # The fork's quoted write lands; its unquoted one is blocked and the fork sees the block text.
    assert r["memory_md"].strip() == f'User decided to ship on Friday ("{DECIDE}")'
    assert json.loads(r["tool_results"][0])["success"] is True
    assert tool_error(r["tool_results"][1]) == block_text("The user approved a Saturday hotfix window.")
    # The fork's tool calls carry the parent session id (agent/background_review.py sets it).
    parent = hashlib.sha256(r["session_id"].encode()).hexdigest()[:12]
    assert [(row["verdict"], row["session"]) for row in r["evidence"]] == [("pass", parent), ("blocked", parent)]
    assert r["evidence"][0]["matched_in"] == "this-session"
    # The fork does not fire pre_llm_call, so its review prompt never becomes "what the user said".
    assert [row["text"] for row in r["said"]] == [DECIDE]
    assert r["review_prompt"] and all(r["review_prompt"] not in row["text"] for row in r["said"])


def test_5_curator_fork(stream, tmp_path_factory):
    r = run("curator", stream, tmp_path_factory)
    assert r["curator_error"] is None
    assert tool_error(r["tool_results"][0]) == block_text("The user decided releases happen on Fridays.")
    assert json.loads(r["tool_results"][1])["success"] is True
    assert "curated-lint/SKILL.md" in r["skills"]
    assert not any(path.startswith("curated-release/") for path in r["skills"])
    [row] = r["evidence"]
    assert (row["tool"], row["target"], row["verdict"]) == ("skill_manage", "skill:curated-release", "blocked")
    assert r["said"] == []  # the curator's prompt (platform "curator") is never captured


def test_6_skill_manage_shapes(stream, tmp_path_factory):
    r = run("skill_shapes", stream, tmp_path_factory)
    results = r["tool_results"]
    assert json.loads(results[0])["success"] is True  # create with a quoted claim
    assert tool_error(results[1]) == block_text("The user approved skipping CI on docs-only changes.")  # patch
    assert json.loads(results[2])["success"] is True  # patch with a quoted claim
    assert tool_error(results[3]) == block_text("The owner approved disabling the linter.")  # write_file
    assert json.loads(results[4])["success"] is True  # write_file without a claim
    assert json.loads(results[5])["success"] is True  # create without a claim
    skill_md = r["skills"]["lint-first/SKILL.md"]
    assert '("From now on always run the linter before committing")' in skill_md
    assert '("always run the linter before committing")' in skill_md
    assert "skipping CI" not in skill_md
    assert "lint-first/references/commands.md" in r["skills"]
    assert "lint-first/references/decisions.md" not in r["skills"]
    assert "plain-notes/SKILL.md" in r["skills"]
    assert [row["verdict"] for row in r["evidence"]] == ["pass", "blocked", "pass", "blocked"]


def test_7_observe_mode_blocks_nothing(stream, tmp_path_factory):
    r = run("observe", stream, tmp_path_factory)
    assert r["memory_md"].strip() == "User decided to ship on Friday."
    assert json.loads(r["tool_results"][0])["success"] is True
    [row] = r["evidence"]
    assert (row["verdict"], row["reason"]) == ("would_block", "no_quote")


def test_8_non_claim_writes_pass_untouched(stream, tmp_path_factory):
    r = run("non_claim", stream, tmp_path_factory)
    assert r["user_md"].strip() == "User prefers dark mode."
    assert r["memory_md"].strip() == "Project uses uv for Python environments."
    assert all(json.loads(t)["success"] is True for t in r["tool_results"])
    assert r["evidence"] == []


def test_9_subagent_goal_is_not_evidence(stream, tmp_path_factory):
    r = run("subagent", stream, tmp_path_factory)
    # The parent model wrote the child's goal; capture skips platform "subagent".
    assert [row["text"] for row in r["said"]] == ["please write the release note"]
    assert tool_error(r["tool_results"][0]).startswith("provenance-gate: this write states a user decision")
    [row] = r["evidence"]
    assert (row["verdict"], row["reason"], row["target"]) == ("blocked", "quote_not_found", "skill:release-note")
    assert not any(path.startswith("release-note/") for path in r["skills"])
