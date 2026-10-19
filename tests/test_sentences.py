from provenance_gate.claims import find_claims, split_sentences


def test_english_sentences():
    assert split_sentences("Use pnpm. The user decided to ship! Is it done? Yes; ok") == [
        "Use pnpm.", "The user decided to ship!", "Is it done?", "Yes;", "ok"]


def test_decimals_and_abbreviated_versions_do_not_split():
    assert split_sentences("Python 3.14 is required. Use v2.1.0 now.") == ["Python 3.14 is required.", "Use v2.1.0 now."]


def test_chinese_sentences():
    assert split_sentences("用户决定周五发布。测试通过了！还要部署吗？好；继续") == [
        "用户决定周五发布。", "测试通过了！", "还要部署吗？", "好；", "继续"]


def test_mixed_language_line():
    assert split_sentences("Ship on Friday. 用户已确认。Done.") == ["Ship on Friday.", "用户已确认。", "Done."]


def test_markdown_lists_headings_and_quotes():
    text = "# Notes\n- User decided to ship.\n* second item\n+ third\n1. numbered\n2) also\n> quoted line\n"
    assert split_sentences(text) == ["Notes", "User decided to ship.", "second item", "third", "numbered", "also",
                                     "quoted line"]


def test_markdown_table_rows_split_into_cells():
    text = "| key | value |\n|-----|-------|\n| owner | The user approved it |"
    assert "The user approved it" in split_sentences(text)
    assert "owner" in split_sentences(text)


def test_sentence_end_inside_quotes_does_not_split():
    text = 'User decided to ship ("Ship it. Now. Please.") today. Next one.'
    assert split_sentences(text) == ['User decided to ship ("Ship it. Now. Please.") today.', "Next one."]
    text = "用户决定发布（「今天发。别等了。」）。下一句。"
    assert split_sentences(text) == ["用户决定发布（「今天发。别等了。」）。", "下一句。"]


def test_crlf_and_cr_line_endings():
    assert split_sentences("first line\r\nsecond line\rthird") == ["first line", "second line", "third"]
    claims = find_claims("Fact one.\r\nThe user approved the plan.\r\n")
    assert [c.sentence for c in claims] == ["The user approved the plan."]


def test_find_claims_returns_each_claim_sentence():
    text = "Env: Ubuntu 24.04.\nThe user decided to use uv. The owner approved the budget. Done."
    assert [c.pattern for c in find_claims(text)] == ["decided", "approved"]
