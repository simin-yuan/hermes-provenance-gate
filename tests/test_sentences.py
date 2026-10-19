from provenance_gate.claims import CODE, PROSE, TABLE, find_claims, split_sentences


def texts(text, context=PROSE):
    return [s.text for s in split_sentences(text) if s.context == context]


def test_english_sentences():
    assert texts("Use pnpm. The user decided to ship! Is it done? Yes; ok") == [
        "Use pnpm.", "The user decided to ship!", "Is it done?", "Yes;", "ok"]


def test_decimals_and_versions_do_not_split():
    assert texts("Python 3.14 is required. Use v2.1.0 now.") == ["Python 3.14 is required.", "Use v2.1.0 now."]


def test_chinese_sentences():
    assert texts("用户决定周五发布。测试通过了！还要部署吗？好；继续") == [
        "用户决定周五发布。", "测试通过了！", "还要部署吗？", "好；", "继续"]


def test_mixed_language_line():
    assert texts("Ship on Friday. 用户已确认。Done.") == ["Ship on Friday.", "用户已确认。", "Done."]


def test_markdown_lists_headings_and_quotes():
    text = "# Notes\n- User decided to ship.\n* second item\n+ third\n1. numbered\n2) also\n> quoted line\n"
    assert texts(text) == ["Notes", "User decided to ship.", "second item", "third", "numbered", "also", "quoted line"]


def test_table_rows_are_table_context():
    text = "| step | file |\n|------|------|\n| User confirmed the order | checkout.js |"
    assert texts(text) == []
    assert "| User confirmed the order | checkout.js |" in texts(text, TABLE)
    assert find_claims(text) == []


def test_fenced_and_indented_code_is_code_context():
    text = ("Notes:\n```python\n# user confirmed the modal\nif user_confirmed:\n    emit()\n```\n"
            "    # the owner approved this branch\n\tuser decided = True\nAfter the fence.")
    assert texts(text) == ["Notes:", "After the fence."]
    assert "# user confirmed the modal" in texts(text, CODE)
    assert "# the owner approved this branch" in texts(text, CODE)
    assert find_claims(text) == []


def test_sentence_end_inside_quotes_does_not_split():
    text = 'User decided to ship ("Ship it. Now. Please.") today. Next one.'
    assert texts(text) == ['User decided to ship ("Ship it. Now. Please.") today.', "Next one."]
    text = "用户决定发布（「今天发。别等了。」）。下一句。"
    assert texts(text) == ["用户决定发布（「今天发。别等了。」）。", "下一句。"]


def test_crlf_and_cr_line_endings():
    assert texts("first line\r\nsecond line\rthird") == ["first line", "second line", "third"]
    found = find_claims("Fact one.\r\nThe user approved the plan.\r\n")
    assert [c.sentence for c in found] == ["The user approved the plan."]


def test_find_claims_returns_each_claim_sentence():
    text = "Env: Ubuntu 24.04.\nThe user decided to use uv. The owner approved the budget. Done."
    assert [c.pattern for c in find_claims(text)] == ["decided", "approved"]
