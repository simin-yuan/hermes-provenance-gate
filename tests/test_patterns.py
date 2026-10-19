"""Every claim pattern matches its examples; every negated or open form does not."""

from __future__ import annotations

import pytest

from provenance_gate import claims, patterns
from provenance_gate.claims import SCOPE_DECISIONS, SCOPE_WITH_PREFERENCES, find_claim


def _examples(table):
    return [(name, example) for name, (_regex, examples) in table.items() for example in examples]


DECISIONS = _examples(patterns.EN_DECISION_VERBS) + _examples(patterns.ZH_DECISION_VERBS)
PREFERENCES = _examples(patterns.EN_PREFERENCE_VERBS) + _examples(patterns.ZH_PREFERENCE_VERBS)
OPEN_FORMS = [(name, example) for name, examples in patterns.OPEN_FORM_EXAMPLES.items() for example in examples]


def test_core_sets_are_complete():
    assert set(patterns.EN_DECISION_VERBS) == {
        "decided", "chose", "agreed", "approved", "confirmed", "signed off", "instructed", "asked me to",
        "told me to", "wants us to", "said to", "gave the go-ahead", "rejected", "vetoed"}
    assert set(patterns.ZH_DECISION_VERBS) == {
        "决定", "同意", "批准", "确认", "拍板", "要求", "指示", "让我", "选择了", "否决", "拒绝了"}
    for name in ("has not decided", "hasn't approved", "didn't agree", "may decide", "will decide later",
                 "asked whether", "to be confirmed", "未决定", "还没确认", "尚未同意", "待确认", "是否同意"):
        assert name in patterns.OPEN_FORM_EXAMPLES


@pytest.mark.parametrize("name,sentence", DECISIONS)
def test_decision_pattern_matches(name, sentence):
    claim = find_claim(sentence, SCOPE_DECISIONS)
    assert claim is not None and claim.pattern == name and claim.kind == "decision"


@pytest.mark.parametrize("name,sentence", PREFERENCES)
def test_preference_pattern_only_in_preference_scope(name, sentence):
    assert find_claim(sentence, SCOPE_DECISIONS) is None
    claim = find_claim(sentence, SCOPE_WITH_PREFERENCES)
    assert claim is not None and claim.pattern == name and claim.kind == "preference"


@pytest.mark.parametrize("name,sentence", OPEN_FORMS)
@pytest.mark.parametrize("scope", claims.SCOPES)
def test_negated_and_open_forms_are_not_claims(name, sentence, scope):
    assert find_claim(sentence, scope) is None


@pytest.mark.parametrize("sentence", [
    "Has the user decided?", "Did the owner approve it?", "Whether the user approved is unclear.",
    "The user may approve it.", "The user might decide later.", "The user doesn't like tabs.",
    "The user does not prefer YAML.",
])
def test_open_preference_forms_are_not_claims(sentence):
    assert find_claim(sentence, SCOPE_WITH_PREFERENCES) is None


@pytest.mark.parametrize("sentence", [
    "He decided to ship on Friday.", "She approved the budget.", "They agreed to move the launch.",
    "I decided to retry.", "We confirmed the fix.", "You chose Postgres.",
])
def test_pronouns_alone_do_not_trigger(sentence):
    assert find_claim(sentence) is None


@pytest.mark.parametrize("sentence", [
    "The build confirmed the fix.", "CI decided to retry the job.", "The repo owner approved the PR.",
    "Product owner approved the budget.", "The user's manager decided to wait.", "The user-facing API confirmed it.",
    "No user approved this.", "The users decided nothing.", "测试已确认通过。", "用户界面已确认可用。",
    "用户名确认无误。", "The user interface rejected the input.",
])
def test_non_user_subjects_do_not_trigger(sentence):
    assert find_claim(sentence) is None


@pytest.mark.parametrize("sentence", [
    "Update: user decided to drop Windows 7.", "After review, the user approved the plan.",
    "Note that the owner vetoed the rename.", "**User decided** to freeze the API.",
    "The user has now explicitly approved the rollout.", "THE USER DECIDED TO SHIP.",
    "Yesterday the owner signed off on the copy.", "My user decided to rename the repo.",
    "用户已经明确同意了。", "老板最终拍板了。",
])
def test_claim_variants(sentence):
    assert find_claim(sentence) is not None


def test_claim_text_inside_quotes_or_code_is_not_a_claim():
    assert find_claim('The changelog says "the user decided" in one place.') is None
    assert find_claim("Grep for `user approved` in the audit log.") is None
    assert find_claim("文档里写着「用户决定」三个字。") is None


def test_question_detection_ignores_quoted_question():
    claim = find_claim('User decided to ship on Friday ("should we just ship on Friday?").')
    assert claim is not None and claim.pattern == "decided"
