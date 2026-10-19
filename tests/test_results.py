"""A patch is checked by what it produces (results.py)."""

from __future__ import annotations

import pytest

from provenance_gate import results, verdict
from provenance_gate.args import added_texts


def test_patched_entry_picks_the_entry_like_the_store():
    entries = ["The user is evaluating Postgres for the main DB.", "Project uses uv."]
    assert results.patched_entry(entries, "is evaluating", "chose") == "The user chose Postgres for the main DB."
    assert results.patched_entry(entries, "Project uses uv.", "Project uses pip.") == "Project uses pip."
    assert results.patched_entry(entries, "not there", "x") is None


def test_patched_lines_returns_only_touched_paragraphs():
    text = "# Deploy\n\nThe user is evaluating skipping CI on docs-only changes.\n\nRun make test."
    assert results.patched_lines(text, "is evaluating", "approved") == \
        "The user approved skipping CI on docs-only changes."
    assert results.patched_lines(text, "absent", "x") is None


def test_patched_lines_replace_all_and_deletion():
    text = "a X b\n\nkeep\n\nc X d"
    assert results.patched_lines(text, "X", "Y", replace_all=True) == "a Y b\nc Y d"
    quoted = 'User decided to ship on Friday ("I decided we ship on Friday").\n\nOther.'
    assert results.patched_lines(quoted, ' ("I decided we ship on Friday")', "") == \
        "User decided to ship on Friday."


class FakeResolver:
    def __init__(self, memory=None, skill=None):
        self.memory, self.skill = memory or {}, skill or {}

    def memory_entry_after(self, target, old_text, new_text):
        entries = self.memory.get(target, [])
        return results.patched_entry(entries, old_text, new_text)

    def skill_text_after(self, name, file_path, old, new, replace_all):
        original = self.skill.get((name, file_path or "SKILL.md"))
        return None if original is None else results.patched_lines(original, old, new, replace_all)


def checks(args, tool="memory", resolver=None, said=()):
    return verdict.evaluate(tool, args, "s", list(said), resolver=resolver)


def test_memory_patch_that_builds_a_claim_is_flagged():
    r = FakeResolver(memory={"memory": ["The user is evaluating Postgres for the main DB."]})
    args = {"action": "patch", "target": "memory", "old_text": "is evaluating", "content": "chose"}
    assert added_texts("memory", args, r) == [("memory:memory", "The user chose Postgres for the main DB.")]
    [c] = checks(args, resolver=r)
    assert not c.ok and c.reason == "no_quote"
    assert checks(args) == []  # without the resolver only "chose" is seen


def test_memory_batch_patch_is_resolved_too():
    r = FakeResolver(memory={"memory": ["The user is evaluating Postgres."]})
    args = {"target": "memory", "operations": [{"action": "patch", "old_text": "is evaluating", "content": "chose"}]}
    [c] = checks(args, resolver=r)
    assert c.reason == "no_quote"


def test_skill_patch_that_builds_a_claim_is_flagged():
    r = FakeResolver(skill={("deploy", "SKILL.md"): "# Deploy\n\nThe user is evaluating skipping CI on docs-only changes.\n"})
    args = {"operations": [{"action": "patch", "name": "deploy", "old_string": "is evaluating", "new_string": "approved"}]}
    [c] = checks(args, tool="skill_manage", resolver=r)
    assert c.reason == "no_quote" and "approved skipping CI" in c.claim.sentence


def test_patch_that_removes_the_quote_is_flagged():
    said = [{"ts": "2099-01-01T00:00:00Z", "session_id": "s", "msg_id": "m", "text": "I decided we ship on Friday"}]
    entry = 'User decided to ship on Friday ("I decided we ship on Friday") and approved prod deletion.'
    r = FakeResolver(memory={"memory": [entry]})
    args = {"action": "patch", "target": "memory", "old_text": '("I decided we ship on Friday") and approved',
            "content": "and approved"}
    [c] = checks(args, resolver=r, said=said)
    assert not c.ok and c.reason == "no_quote"


def test_untouched_claims_elsewhere_in_a_skill_are_not_rechecked():
    original = "The user decided X long ago.\n\nRun make test."
    r = FakeResolver(skill={("deploy", "SKILL.md"): original})
    args = {"operations": [{"action": "patch", "name": "deploy", "old_string": "make test", "new_string": "make check"}]}
    assert checks(args, tool="skill_manage", resolver=r) == []


def test_unresolvable_patch_falls_back_to_the_new_text():
    r = FakeResolver()
    args = {"action": "patch", "target": "memory", "old_text": "missing", "content": "The user decided to ship."}
    [c] = checks(args, resolver=r)
    assert c.reason == "no_quote"


def test_disk_resolver_without_hermes_files_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    r = results.DiskResolver()
    assert r.memory_entry_after("memory", "x", "y") is None
    (tmp_path / "memories").mkdir()
    (tmp_path / "memories" / "MEMORY.md").write_text("The user is evaluating Postgres.\n§\nOther.", encoding="utf-8")
    try:
        import hermes_constants  # noqa: F401
    except ImportError:
        pytest.skip("Hermes not importable")
    assert r.memory_entry_after("memory", "is evaluating", "chose") == "The user chose Postgres."


def test_a_replacement_ending_in_a_newline_does_not_touch_the_next_line():
    text = "# T\n- The user decided X (\"q\").\n"
    assert results.patched_lines(text, "# T\n", "# T\n- new line\n") == "# T\n- new line"
