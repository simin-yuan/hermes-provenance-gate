from __future__ import annotations

import argparse
import json
from datetime import timedelta

import pytest

from provenance_gate import capture, cli, store
from provenance_gate.hooks import Gate


@pytest.fixture()
def run(tmp_path, monkeypatch):
    for name in ("PROVENANCE_GATE_MODE", "PROVENANCE_GATE_SEARCH", "PROVENANCE_GATE_SCOPE"):
        monkeypatch.delenv(name, raising=False)
    parser = argparse.ArgumentParser(prog="hermes provenance-gate")
    cli.setup(parser)
    handler = cli.make_handler(lambda: tmp_path)

    def invoke(*argv):
        return handler(parser.parse_args(list(argv)))
    return invoke


def test_check_pass_and_block(run, tmp_path, capsys):
    capture.capture(tmp_path, "s1", "I decided we ship on Friday")
    before = sorted(p.name for p in tmp_path.iterdir())
    assert run("check", 'User decided to ship on Friday ("I decided we ship on Friday")', "--session", "s1") == 0
    out = capsys.readouterr().out
    assert "pass" in out and "this-session" in out
    assert run("check", "The user approved the budget.") == 1
    out = capsys.readouterr().out
    assert "no_quote" in out and "verdict: block" in out and "Nothing was written." in out
    assert run("check", "Project uses uv.") == 0
    assert "no decision claims" in capsys.readouterr().out
    assert sorted(p.name for p in tmp_path.iterdir()) == before  # dry run: nothing written


def test_log_filters(run, tmp_path, capsys):
    gate = Gate(directory=lambda: tmp_path)
    gate.pre_llm_call(session_id="s", user_message="I decided we ship on Friday")
    gate.pre_tool_call(tool_name="memory", args={"action": "add", "target": "memory",
                       "content": 'User decided to ship on Friday ("I decided we ship on Friday")'}, session_id="s")
    for _ in range(3):
        gate.pre_tool_call(tool_name="memory", args={"action": "add", "target": "memory",
                           "content": "User decided to ship."}, session_id="s")
    capsys.readouterr()
    assert run("log") == 0
    assert len(capsys.readouterr().out.strip().splitlines()) == 4
    run("log", "--verdict", "blocked", "--limit", "2")
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2 and all(json.loads(line)["verdict"] == "blocked" for line in lines)
    run("log", "--verdict", "would_block")
    assert capsys.readouterr().out.strip() == "no evidence rows"


def test_purge(run, tmp_path, capsys):
    now = store.utc_now()
    capture.capture(tmp_path, "s", "old one", now=now - timedelta(days=10))
    capture.capture(tmp_path, "s", "new one", now=now)
    assert run("purge", "--older-than", "5") == 0
    assert [r["text"] for r in capture.said_rows(tmp_path)] == ["new one"]
    assert "said.jsonl: removed 1 row(s)" in capsys.readouterr().out
    assert run("purge", "--all") == 0
    assert capture.said_rows(tmp_path) == []


def test_purge_requires_a_choice(run):
    with pytest.raises(SystemExit):
        run("purge")


@pytest.mark.parametrize("argv", [("purge", "--older-than", "-1"), ("log", "--limit", "-5"),
                                  ("log", "--limit", "two")])
def test_negative_or_bad_numbers_are_rejected(run, argv, capsys):
    with pytest.raises(SystemExit):
        run(*argv)
    err = capsys.readouterr().err
    assert "is negative" in err or "is not a whole number" in err


def test_no_subcommand(run, capsys):
    assert run() == 2
    assert "usage" in capsys.readouterr().out
