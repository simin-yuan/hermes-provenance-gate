"""A patch is checked by what it produces (results.py)."""

from __future__ import annotations

import pytest

from provenance_gate import results, verdict
from provenance_gate.args import added_texts as _added_texts


def added_texts(*a, **k):
    return [(target, text) for target, text, _before in _added_texts(*a, **k)]


def test_patched_entry_picks_the_entry_like_the_store():
    entries = ["The user is evaluating Postgres for the main DB.", "Project uses uv."]
    assert results.patched_entry(entries, "is evaluating", "chose") == (0, "The user chose Postgres for the main DB.")
    assert results.patched_entry(entries, "Project uses uv.", "Project uses pip.") == (1, "Project uses pip.")
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

    def memory_entries(self, target):
        return list(self.memory.get(target, []))

    def skill_file(self, name, file_path):
        return self.skill.get((name, file_path or "SKILL.md"))


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
    assert not r.memory_entries("memory")
    (tmp_path / "memories").mkdir()
    (tmp_path / "memories" / "MEMORY.md").write_text("The user is evaluating Postgres.\n§\nOther.", encoding="utf-8")
    try:
        import hermes_constants  # noqa: F401
    except ImportError:
        pytest.skip("Hermes not importable")
    assert r.memory_entries("memory") == ["The user is evaluating Postgres.", "Other."]


def test_a_replacement_ending_in_a_newline_does_not_touch_the_next_line():
    text = "# T\n- The user decided X (\"q\").\n"
    assert results.patched_lines(text, "# T\n", "# T\n- new line\n") == "# T\n- new line"


def test_add_then_patch_in_one_batch_is_replayed():
    r = FakeResolver()
    args = {"target": "memory", "operations": [
        {"action": "add", "content": "The user is evaluating Postgres for the main DB."},
        {"action": "patch", "old_text": "is evaluating", "content": "chose"}]}
    [c] = checks(args, resolver=r)
    assert c.reason == "no_quote" and c.claim.sentence == "The user chose Postgres for the main DB."


def test_skill_create_then_patch_in_one_call_is_replayed():
    r = FakeResolver()
    args = {"operations": [
        {"action": "create", "name": "a", "content": "# A\n- The user is evaluating skipping CI.\n"},
        {"action": "patch", "name": "a", "old_string": "is evaluating", "new_string": "approved"}]}
    [c] = checks(args, tool="skill_manage", resolver=r)
    assert c.reason == "no_quote" and "approved skipping CI" in c.claim.sentence


def test_fuzzy_whitespace_and_unicode_patch_spans_are_found():
    assert results.patched_lines("- The user is   evaluating Postgres.\n", "is evaluating", "chose") == "- The user chose Postgres."
    text = "- User decided to ship on Friday (\u201cI decided we ship on Friday\u201d).\n"
    after = results.patched_lines(text, ' ("I decided we ship on Friday")', "")
    assert after.startswith("- User decided to ship on Friday") and "I decided" not in after
    r = FakeResolver(skill={("s", "SKILL.md"): text})
    args = {"operations": [{"action": "patch", "name": "s", "old_string": ' ("I decided we ship on Friday")', "new_string": ""}]}
    [c] = checks(args, tool="skill_manage", resolver=r)
    assert c.reason == "no_quote"


def test_editing_an_old_quoted_entry_does_not_recheck_the_old_claim():
    entry = 'Owner: Ana. The user decided to ship on Friday ("I decided we ship on Friday").'
    r = FakeResolver(memory={"memory": [entry]})
    args = {"action": "patch", "target": "memory", "old_text": "Owner: Ana.", "content": "Owner: Anna."}
    assert checks(args, resolver=r) == []  # no session text needed: the claim sentence is unchanged
    consolidation = {"target": "memory", "operations": [{"action": "remove", "old_text": "Owner: Ana."},
                                                        {"action": "add", "content": entry}]}
    assert checks(consolidation, resolver=r) == []


def test_an_edit_that_keeps_a_stored_quote_passes_in_a_new_session():
    entry = 'User decided to ship on Friday ("I decided we ship on Friday").'
    r = FakeResolver(memory={"memory": [entry, "Release owner: Anna"]})
    args = {"target": "memory", "operations": [
        {"action": "remove", "old_text": "Release owner: Anna"},
        {"action": "patch", "old_text": "ship on Friday (", "content": "ship on Friday, release owner Anna ("}]}
    [c] = checks(args, resolver=r)
    assert c.ok and c.matched_in == "stored-text"
    # a stored quote does not back an unrelated new claim
    args = {"action": "patch", "target": "memory", "old_text": "User decided to ship on Friday",
            "content": "The user approved deleting prod"}
    [c] = checks(args, resolver=r)
    assert not c.ok and c.reason == "quote_unrelated"
