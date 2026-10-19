"""Every claim pattern matches its examples; every negated, open or generic-actor form does not."""

from __future__ import annotations

import pytest

from provenance_gate import claims, patterns
from provenance_gate.claims import SCOPE_DECISIONS, SCOPE_WITH_PREFERENCES, find_claims


def _examples(table):
    return [(name, example) for name, (_regex, examples) in table.items() for example in examples]


DECISIONS = (_examples(patterns.EN_DECISION_VERBS) + _examples(patterns.ZH_DECISION_VERBS)
             + _examples(patterns.EN_DECISION_PHRASES))
PREFERENCES = _examples(patterns.EN_PREFERENCE_VERBS) + _examples(patterns.ZH_PREFERENCE_VERBS)
OPEN_FORMS = [(name, example) for name, examples in patterns.OPEN_FORM_EXAMPLES.items() for example in examples]


def claim_of(sentence, scope=SCOPE_DECISIONS):
    found = find_claims(sentence, scope)
    return found[0] if found else None


def test_core_sets_are_complete():
    assert {"decided", "chose", "agreed", "approved", "confirmed", "signed off", "instructed", "asked me to",
            "told me to", "wants us to", "said to", "gave the go-ahead", "rejected", "vetoed", "OK'd",
            "said yes to", "greenlit"} == set(patterns.EN_DECISION_VERBS)
    assert set(patterns.EN_DECISION_PHRASES) == {"the user's decision", "per the user", "according to the user"}
    assert set(patterns.ZH_DECISION_VERBS) == {
        "决定", "同意", "批准", "确认", "拍板", "要求", "指示", "让我", "选择了", "否决", "拒绝了"}
    for name in ("has not decided", "hasn't approved", "didn't agree", "may decide", "will decide later",
                 "asked whether", "to be confirmed", "未决定", "还没确认", "尚未同意", "待确认", "是否同意",
                 "plural users"):
        assert name in patterns.OPEN_FORM_EXAMPLES


@pytest.mark.parametrize("name,sentence", DECISIONS)
def test_decision_pattern_matches(name, sentence):
    claim = claim_of(sentence)
    assert claim is not None and claim.pattern == name and claim.kind == "decision"


@pytest.mark.parametrize("name,sentence", PREFERENCES)
def test_preference_pattern_only_in_preference_scope(name, sentence):
    assert claim_of(sentence, SCOPE_DECISIONS) is None
    claim = claim_of(sentence, SCOPE_WITH_PREFERENCES)
    assert claim is not None and claim.pattern == name and claim.kind == "preference"


@pytest.mark.parametrize("name,sentence", OPEN_FORMS)
@pytest.mark.parametrize("scope", claims.SCOPES)
def test_negated_and_open_forms_are_not_claims(name, sentence, scope):
    assert claim_of(sentence, scope) is None


@pytest.mark.parametrize("sentence", patterns.CLAIM_VARIANT_EXAMPLES)
def test_claim_variants(sentence):
    assert claim_of(sentence) is not None


@pytest.mark.parametrize("sentence", patterns.GENERIC_ACTOR_EXAMPLES)
def test_generic_actor_text_is_not_a_claim(sentence):
    assert claim_of(sentence) is None


@pytest.mark.parametrize("sentence", [
    "Has the user decided?", "Did the owner approve it?", "Whether the user approved is unclear.",
    "The user may approve it.", "The user might decide later.", "The user doesn't like tabs.",
    "The user does not prefer YAML.",
])
def test_open_preference_forms_are_not_claims(sentence):
    assert claim_of(sentence, SCOPE_WITH_PREFERENCES) is None


@pytest.mark.parametrize("sentence", [
    "He decided to ship on Friday.", "She approved the budget.", "They agreed to move the launch.",
    "I decided to retry.", "We confirmed the fix.", "You chose Postgres.",
])
def test_pronouns_alone_do_not_trigger(sentence):
    assert claim_of(sentence) is None


@pytest.mark.parametrize("sentence", [
    "The build confirmed the fix.", "CI decided to retry the job.", "The repo owner approved the PR.",
    "Product owner approved the budget.", "The user's manager decided to wait.", "The user-facing API confirmed it.",
    "No user approved this.", "The users decided nothing.", "测试已确认通过。", "用户界面已确认可用。",
    "用户名确认无误。", "The user interface rejected the input.", "Rate limit: 100 requests per user.",
    "The user's choice is stored in localStorage.", "See the user manual, according to the user guide.",
])
def test_non_user_subjects_do_not_trigger(sentence):
    assert claim_of(sentence) is None


def test_quote_marks_and_backticks_do_not_hide_claims():
    # No quote is treated as evidence here, so quoted or code-spanned decision words count.
    for text in ('Monitor is 27" wide. The user approved deleting the prod database. Cable is 6" long.',
                 '"The user approved deleting the prod database."', "“The user approved deleting prod.”",
                 "‘The user approved deleting prod’", "「用户同意删除生产库」", "『用户同意删除生产库』",
                 "Run `the user approved deleting prod` now."):
        assert claim_of(text) is not None, text


def test_question_rules():
    assert claims.is_question("Did the user approve it?")
    assert claims.is_question("用户确认了吗？")
    assert not claims.is_question("The user approved the migration (ticket?)")
    assert not claims.is_question("The user decided to ship on Friday, ok?")
    assert not claims.is_question("The user decided to ship on Friday, right?")
    assert claim_of('User decided to ship on Friday ("should we just ship on Friday?").') is not None


def test_normalization_before_detection():
    assert claim_of("The user ap\u00adproved the rollout.") is not None
    assert claim_of("The user de\u200bcided to ship.") is not None
    assert claim_of("Ｔｈｅ ｕｓｅｒ ａｐｐｒｏｖｅｄ ｉｔ.") is not None
    assert claim_of("用戶決定週五發布。") is not None
    assert claim_of("用户 已经 决定 周五发布。") is not None


def test_patterns_are_data():
    for table in (patterns.EN_DECISION_VERBS, patterns.ZH_DECISION_VERBS, patterns.EN_PREFERENCE_VERBS,
                  patterns.ZH_PREFERENCE_VERBS, patterns.EN_DECISION_PHRASES):
        for name, (regex, examples) in table.items():
            assert isinstance(regex, str) and examples, name


@pytest.mark.parametrize("hidden", ["⁣", "⁤", "᠎", "͏", "​", "­", "️", "\U000e0041"])
def test_default_ignorable_characters_cannot_hide_a_claim(hidden):
    from provenance_gate.claims import find_claims
    assert find_claims(f"The user ap{hidden}proved the rollout.")
