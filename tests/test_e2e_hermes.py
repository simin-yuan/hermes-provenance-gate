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
import shutil
import subprocess
import sys
import tempfile
import time
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


def _require_free_space(path: Path, need_gb: float = 3.0) -> None:
    """Fail fast instead of filling the drive.

    Every e2e scenario installs the plugin into its own throwaway HERMES_HOME, so a full
    suite writes a few hundred MB to a few GB depending on what the package manager already
    cached. On 2026-10-22 a run on a drive with ~0 free filled it and broke unrelated
    writes in the host system; a clear failure up front is cheaper than that.
    """
    free = shutil.disk_usage(path).free / 2**30
    assert free >= need_gb, (
        f"only {free:.1f} GB free on the drive holding the temp dir; an e2e run needs "
        f">= {need_gb:.0f} GB. Free space first (an interrupted run may have left pg-e2e-* dirs)."
    )


def _shared_installs() -> Path:
    """The one runtime store every scenario links to.

    Each worker used to install a full Hermes runtime into its own temp home (~630 MB,
    which blew past the 600 s worker timeout on 2026-10-23 and never finished). Sharing
    the store makes the first run pay that cost once; the rest start immediately.
    """
    explicit = os.environ.get("PG_E2E_SHARED_INSTALLS")
    if explicit:
        return Path(explicit)
    live = Path(os.environ.get("HERMES_HOME") or (Path.home() / "AppData" / "Local" / "hermes")) / "installs"
    if live.is_dir():
        return live
    return Path(tempfile.gettempdir()) / "pg-e2e-shared-installs"


def _link_installs(home: Path) -> None:
    link = home / "installs"
    if link.exists():
        return
    shared = _shared_installs()
    shared.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(shared)],
                       check=True, capture_output=True)
    else:
        os.symlink(shared, link, target_is_directory=True)


def _unlink_junctions(path: Path) -> None:
    """Drop junctions/symlinks before a recursive delete.

    ``rd /s /q`` walks through a junction into its target, so cleaning a temp home
    would otherwise wipe the shared runtime that every scenario points at.
    """
    if not path.is_dir():
        return
    for child in list(path.iterdir()):
        try:
            # ``os.path.isjunction`` only exists on 3.12+, and this suite runs on the
            # Hermes venv's 3.11, so test the reparse-point attribute instead.
            attrs = child.lstat().st_file_attributes
            is_link = child.is_symlink() or bool(attrs & 0x400)
        except (OSError, AttributeError):
            continue
        if is_link:
            try:
                os.rmdir(child)
            except OSError:
                pass


def _remove_tree(path: Path) -> None:
    """Delete a temp HERMES_HOME, surviving Windows handle latency.

    ``shutil.rmtree`` gives up on the first locked file, and a plugin install can still be
    finishing in a grandchild process while the parent starts deleting -- that is how a
    tree survived after the worker had already exited. Retry briefly, clearing read-only
    bits between attempts.
    """
    def _onerror(func, target, _exc):
        try:
            os.chmod(target, 0o700)
            func(target)
        except OSError:
            pass

    _unlink_junctions(path)
    for delay in (0, 0.5, 1.5):
        if delay:
            time.sleep(delay)
        if not path.exists():
            return
        if os.name == "nt":
            # shutil.rmtree reports success on this tree while leaving it behind, so use the
            # OS tool; ``rd /s /q`` also clears the read-only bit that blocks rmtree.
            subprocess.run(["cmd", "/c", "rd", "/s", "/q", str(path)], capture_output=True)
        else:
            shutil.rmtree(path, onerror=_onerror)
        if not path.exists():
            return


def _hermes_python() -> str:
    """The interpreter Hermes itself runs on, when this machine provisions one.

    The checkout venv and Hermes' app environment can be different minor versions (3.11 vs
    3.14 here), and a worker started on the wrong one dies on a C extension
    (``pydantic_core._pydantic_core``) rather than on anything this test is about.
    ``facts.json`` records the environment Hermes committed to.
    """
    root = Path(os.environ.get("HERMES_HOME") or (Path.home() / "AppData" / "Local" / "hermes"))
    best: tuple[float, Path] | None = None
    for facts in root.glob("installs/*/facts.json"):
        try:
            data = json.loads(facts.read_text(encoding="utf-8-sig"))
            env_dir = ((data.get("packages") or {}).get("venv") or {}).get("environment")
        except (OSError, ValueError):
            continue
        if not env_dir:
            continue
        exe = Path(env_dir) / "Scripts" / "python.exe"
        if exe.is_file():
            mtime = facts.stat().st_mtime
            if best is None or mtime > best[0]:
                best = (mtime, exe)
    return str(best[1]) if best else sys.executable


def _worker_env(home: Path) -> dict:
    """Environment for the worker: its own HERMES_HOME, set before the interpreter starts.

    The worker used to do this itself, after Hermes' bootstrap had already initialised the
    default home. Switching HERMES_HOME mid-process leaves the borrowed-home launch on the
    first interpreter, which cannot see the packages the checkout has
    (``ModuleNotFoundError: No module named 'ruamel'``). Setting it here means the bootstrap
    only ever sees one home.
    """
    env = os.environ.copy()
    env["HERMES_HOME"] = str(home)
    # Hermes' bootstrap syncs dependencies for a borrowed home on the first `agent` import, and
    # a failed sync leaves the process on a half-built environment: `hermes_yaml` then cannot
    # import `ruamel`, even though the checkout venv has it. The sync is what makes this test
    # depend on a reachable package index (`uv sync exited 2: failed to download cffi==2.1.1`),
    # which an end-to-end test should not need — this venv already carries every dependency.
    env["HERMES_DISABLE_LAZY_INSTALLS"] = "1"
    return env


def run(scenario: str, stream: bool, tmp_path_factory) -> dict:
    key = (scenario, stream)
    if key not in _cache:
        _require_free_space(tmp_path_factory.getbasetemp())
        work = tmp_path_factory.mktemp(f"{scenario}-{'stream' if stream else 'plain'}")
        spec, out = work / "spec.json", work / "result.json"
        # The worker's HERMES_HOME is created here rather than in the child so this process
        # can delete it after the child exits: a running interpreter keeps handles inside its
        # own home, so an in-child rmtree leaves the tree behind.
        home = Path(tempfile.mkdtemp(prefix="pg-e2e-"))
        _link_installs(home)
        spec.write_text(json.dumps({"scenario": scenario, "stream": stream, "home": str(home)}),
                        encoding="utf-8")
        try:
            proc = subprocess.run([_hermes_python(), str(WORKER), str(spec), str(out)], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=600,
                                  env=_worker_env(home))
        finally:
            _remove_tree(home)
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
        f'User decided to ship on Friday ("{fake}")',
        f'The quoted words ("{fake}") are not in anything the user typed in this session.')
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


def test_10_string_operations_are_checked(stream, tmp_path_factory):
    r = run("string_operations", stream, tmp_path_factory)
    # Hermes decodes the JSON string later (agent/inline_tool_executors.py:305); the gate decodes it first.
    assert tool_error(r["tool_results"][0]) == block_text("User decided to ship on Friday.")
    assert r["memory_md"] == ""
    assert r["evidence"][0]["reason"] == "no_quote"


def test_11_claim_between_inch_marks_is_blocked(stream, tmp_path_factory):
    r = run("hidden_in_quotes", stream, tmp_path_factory)
    assert tool_error(r["tool_results"][0]).startswith("provenance-gate: this write states a user decision")
    assert r["memory_md"] == ""
    assert r["evidence"][0]["verdict"] == "blocked"


def test_12_unrelated_quote_is_blocked(stream, tmp_path_factory):
    r = run("unrelated_quote", stream, tmp_path_factory)
    message = tool_error(r["tool_results"][0])
    assert "do not share key words with the decision" in message
    assert r["memory_md"] == "" and r["evidence"][0]["reason"] == "quote_unrelated"


def test_13_a_turn_hermes_wrote_is_not_evidence(stream, tmp_path_factory):
    r = run("synthetic_turn", stream, tmp_path_factory)
    assert [row["text"] for row in r["said"]] == [DECIDE]  # the async result was not captured
    assert r["memory_md"] == ""
    assert r["evidence"][0]["reason"] == "quote_not_found"


def test_14_a_patch_that_builds_a_claim_is_blocked(stream, tmp_path_factory):
    r = run("patch_splice", stream, tmp_path_factory)
    assert r["memory_md"].strip() == "The user is evaluating Postgres for the main DB."
    row = r["evidence"][-1]
    assert (row["verdict"], row["reason"], row["claim"]) == ("blocked", "no_quote", "The user chose Postgres for the main DB.")


def test_15_a_quote_that_drops_a_negation_is_blocked(stream, tmp_path_factory):
    r = run("negated_fragment", stream, tmp_path_factory)
    assert r["memory_md"] == ""
    assert r["evidence"][0]["reason"] == "quote_negated"


def test_16_each_claim_needs_its_own_quote(stream, tmp_path_factory):
    r = run("two_claims_one_quote", stream, tmp_path_factory)
    assert r["memory_md"] == ""
    assert [row["reason"] for row in r["evidence"]] == ["no_quote"]


def test_17_a_claim_built_inside_one_batch_is_blocked(stream, tmp_path_factory):
    r = run("batch_splice", stream, tmp_path_factory)
    assert r["memory_md"] == ""
    row = r["evidence"][-1]
    assert (row["verdict"], row["reason"], row["claim"]) == ("blocked", "no_quote", "The user chose Postgres for the main DB.")


def test_18_a_nested_list_item_is_checked(stream, tmp_path_factory):
    r = run("nested_bullet", stream, tmp_path_factory)
    assert r["memory_md"] == ""
    assert r["evidence"][0]["reason"] == "no_quote"


def test_19_a_question_is_not_an_approval(stream, tmp_path_factory):
    r = run("question_as_quote", stream, tmp_path_factory)
    assert r["memory_md"] == ""
    assert r["evidence"][0]["reason"] == "quote_from_question"
