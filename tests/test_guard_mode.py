"""The shared HERMES_GUARD_MODE key, and how it interacts with this plugin's own key and pin.

Every plugin in the family reads one setting, so turning enforcement on is one edit instead of three.
The order is deliberate: the plugin's own key first (nothing configured today changes behaviour),
then the shared key, then the install's own pin.
"""

import pytest

from provenance_gate import verdict

ENV = "PROVENANCE_GATE_MODE"
SHARED = "HERMES_GUARD_MODE"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in (ENV, SHARED):
        monkeypatch.delenv(name, raising=False)


def test_default_is_observe(monkeypatch):
    assert verdict.mode() == verdict.MODE_OBSERVE


def test_own_key_block_unchanged(monkeypatch):
    # The pre-existing spelling must keep working exactly as before.
    monkeypatch.setenv(ENV, "block")
    assert verdict.mode() == verdict.MODE_BLOCK


def test_own_key_ask_unchanged(monkeypatch):
    monkeypatch.setenv(ENV, "ask")
    assert verdict.mode() == verdict.MODE_ASK


def test_own_key_does_not_accept_the_family_word_act(monkeypatch):
    """`act` belongs to the family key only.

    The plugin's own vocabulary is published as observe/ask/block, and a word it does not
    know has always meant "no opinion, use the next source". Teaching the own key the
    family's spelling would quietly change what `PROVENANCE_GATE_MODE=act` does today —
    from observe to block — which is exactly the kind of upgrade-time behaviour change
    that refuses a user's work without them having changed anything.
    """
    monkeypatch.setenv(ENV, "act")
    assert verdict.mode() == verdict.MODE_OBSERVE


def test_own_key_act_still_defers_to_the_shared_key(monkeypatch):
    """Unrecognised own value, so the family setting decides — not the default."""
    monkeypatch.setenv(ENV, "act")
    monkeypatch.setenv(SHARED, "act")
    assert verdict.mode() == verdict.MODE_BLOCK


def test_shared_key_act_enforces(monkeypatch):
    monkeypatch.setenv(SHARED, "act")
    assert verdict.mode() == verdict.MODE_BLOCK


def test_shared_key_block_is_accepted_too(monkeypatch):
    monkeypatch.setenv(SHARED, "block")
    assert verdict.mode() == verdict.MODE_BLOCK


def test_shared_key_ask_escalates(monkeypatch):
    monkeypatch.setenv(SHARED, "ask")
    assert verdict.mode() == verdict.MODE_ASK


def test_shared_key_observe(monkeypatch):
    monkeypatch.setenv(SHARED, "observe")
    assert verdict.mode() == verdict.MODE_OBSERVE


def test_own_key_wins_over_shared_key(monkeypatch):
    """A per-plugin setting is more specific than a family-wide one."""
    monkeypatch.setenv(ENV, "observe")
    monkeypatch.setenv(SHARED, "act")
    assert verdict.mode() == verdict.MODE_OBSERVE


def test_junk_shared_value_is_ignored(monkeypatch):
    monkeypatch.setenv(SHARED, "enforce")
    assert verdict.mode() == verdict.MODE_OBSERVE


def test_shared_value_is_case_and_space_insensitive(monkeypatch):
    monkeypatch.setenv(SHARED, "  ACT  ")
    assert verdict.mode() == verdict.MODE_BLOCK
