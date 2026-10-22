"""Which repository a session touched, and the revision rule: one repo or nothing."""

from __future__ import annotations

import subprocess

from provenance_gate import repos, store
from provenance_gate.hooks import Gate


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                   encoding="utf-8", errors="replace")


def make_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-b", "main")
    git(path, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit",
        "--allow-empty", "-m", "first")
    head = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout.strip()
    return str(path), head


# --- noticing a repository -----------------------------------------------------------------------

def test_a_git_dash_c_command_names_its_repository(tmp_path):
    repo, _ = make_repo(tmp_path / "proj")
    repos.record(tmp_path, "s1", {"command": f"git -C {repo} status --short"})
    assert repos.touched(tmp_path, "s1") == [repo]


def test_a_workdir_inside_the_repository_resolves_to_its_root(tmp_path):
    repo, _ = make_repo(tmp_path / "proj")
    repos.record(tmp_path, "s1", {"workdir": f"{repo}/src/deep", "command": "ls"})
    assert repos.touched(tmp_path, "s1") == [repo]


def test_a_file_path_inside_the_repository_counts(tmp_path):
    repo, _ = make_repo(tmp_path / "proj")
    repos.record(tmp_path, "s1", {"path": f"{repo}/README.md"})
    assert repos.touched(tmp_path, "s1") == [repo]


def test_paths_that_are_not_repositories_are_ignored(tmp_path):
    (tmp_path / "plain").mkdir()
    repos.record(tmp_path, "s1", {"workdir": str(tmp_path / "plain")})
    repos.record(tmp_path, "s1", {"command": "cd relative/dir && ls"})
    assert repos.touched(tmp_path, "s1") == []


def test_bookkeeping_failures_never_escape(tmp_path):
    repos.record(tmp_path, "", {"workdir": str(tmp_path)})          # no session
    repos.record(tmp_path, "s1", {"workdir": object()})             # unhashable junk
    repos.record(tmp_path, "s1", ["not", "a", "dict"])              # wrong shape
    assert repos.touched(tmp_path, "s1") == []


# --- the revision rule ---------------------------------------------------------------------------

def test_no_repository_means_no_revision(tmp_path):
    assert repos.pin(tmp_path, "s1") == ""


def test_two_repositories_means_no_revision(tmp_path):
    one, _ = make_repo(tmp_path / "one")
    two, _ = make_repo(tmp_path / "two")
    repos.record(tmp_path, "s1", {"workdir": one})
    repos.record(tmp_path, "s1", {"workdir": two})
    assert sorted(repos.touched(tmp_path, "s1")) == sorted([one, two])
    assert repos.pin(tmp_path, "s1") == "", "ambiguity must not be resolved by picking one"


def test_exactly_one_repository_pins_its_head(tmp_path):
    repo, head = make_repo(tmp_path / "proj")
    repos.record(tmp_path, "s1", {"command": f"git -C {repo} rev-parse HEAD"})
    assert repos.pin(tmp_path, "s1") == head


def test_a_repository_with_no_commits_yields_no_revision(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    git(empty, "init", "-b", "main")
    assert repos.head_sha(str(empty)) == ""


def test_touches_are_per_session(tmp_path):
    one, head_one = make_repo(tmp_path / "one")
    repos.record(tmp_path, "s1", {"workdir": one})
    assert repos.pin(tmp_path, "s2") == ""
    assert repos.pin(tmp_path, "s1") == head_one


# --- what the gate writes ------------------------------------------------------------------------

def open_claim(gate, content, said, session="s1"):
    gate.pre_llm_call(session_id=session, user_message=said, sender_id="u-1")
    return gate.pre_tool_call(tool_name="memory", args={"action": "add", "target": "memory",
                                                       "content": content}, session_id=session, turn_id="t1")


def test_an_open_condition_gets_a_claim_id_and_a_pinned_revision(tmp_path, monkeypatch):
    monkeypatch.setenv("PROVENANCE_GATE_MODE", "block")
    repo, head = make_repo(tmp_path / "proj")
    gate = Gate(directory=lambda: tmp_path)
    gate.pre_tool_call(tool_name="terminal", args={"command": f"git -C {repo} status"}, session_id="s1")
    said = "We ship on Friday only if the benchmark passes"
    assert open_claim(gate, f'User decided to ship on Friday ("{said}")', said) is None
    [row] = store.read_rows(tmp_path / store.EVIDENCE_FILE)
    assert row["condition"] == "unresolved"
    assert row["claim_id"] and len(row["claim_id"]) == 12
    assert row["revision"] == head


def test_without_one_clear_repository_the_revision_field_is_empty_not_invented(tmp_path, monkeypatch):
    monkeypatch.setenv("PROVENANCE_GATE_MODE", "block")
    gate = Gate(directory=lambda: tmp_path)
    said = "We ship on Friday only if the benchmark passes"
    assert open_claim(gate, f'User decided to ship on Friday ("{said}")', said) is None
    [row] = store.read_rows(tmp_path / store.EVIDENCE_FILE)
    assert row["revision"] == ""


def test_a_plain_claim_carries_no_revision_at_all(tmp_path, monkeypatch):
    monkeypatch.setenv("PROVENANCE_GATE_MODE", "block")
    repo, _ = make_repo(tmp_path / "proj")
    gate = Gate(directory=lambda: tmp_path)
    gate.pre_tool_call(tool_name="terminal", args={"command": f"git -C {repo} status"}, session_id="s1")
    said = "We ship on Friday"
    assert open_claim(gate, f'User decided to ship on Friday ("{said}")', said) is None
    [row] = store.read_rows(tmp_path / store.EVIDENCE_FILE)
    assert row["condition"] == "none" and "revision" not in row
