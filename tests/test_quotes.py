import pytest

from provenance_gate import quotes


@pytest.mark.parametrize("text,expected", [
    ('He said "ship it on friday" yesterday', ["ship it on friday"]),
    ("He said “ship it on friday” yesterday", ["ship it on friday"]),
    ("He said 'ship it on friday' yesterday", ["ship it on friday"]),
    ("He said ‘ship it on friday’ yesterday", ["ship it on friday"]),
    ("他说「周五发布」了", ["周五发布"]),
    ("他说『周五发布』了", ["周五发布"]),
    ('two: "first one here" and “second one here”', ["first one here", "second one here"]),
])
def test_each_quote_style(text, expected):
    assert quotes.extract_quotes(text) == expected


def test_single_quotes_need_two_words():
    assert quotes.extract_quotes("set 'debug' to true") == []
    assert quotes.extract_quotes("the user's 'ship it' message") == ["ship it"]


def test_apostrophes_are_not_quotes():
    assert quotes.extract_quotes("don't do it, it's the user's call") == []
    assert quotes.extract_quotes("he wrote 'don't ship it today' twice") == ["don't ship it today"]


def test_inch_marks_pair_up_as_a_span():
    text = 'Monitor is 27" wide. The user approved it. Cable is 6" long.'
    assert quotes.extract_quotes(text) == [" wide. The user approved it. Cable is 6"]


def test_remove_spans():
    text = 'A ("one two") B “three” C'
    spans = quotes.quote_spans(text)
    assert quotes.remove_spans(text, spans[:1]) == "A ( Q ) B “three” C"


def test_normalization():
    assert quotes.normalize("  Ship\tIt\n NOW ") == "ship it now"
    assert quotes.normalize("ＳＨＩＰ　ｉｔ") == "ship it"  # NFKC: full-width letters and space
    assert quotes.normalize("it’s “done”") == quotes.normalize("it's \"done\"")
    assert quotes.normalize("ap\u00adpro\u200bved") == "approved"
    assert quotes.normalize_quote("We ship on Friday!!") == "we ship on friday"
    assert quotes.normalize_quote("周五发布。") == "周五发布"


def test_occurs_in_respects_latin_word_boundaries():
    msg = quotes.normalize("I decided we ship on Friday")
    assert quotes.occurs_in(quotes.normalize_quote("we ship on Friday."), msg)
    assert not quotes.occurs_in(quotes.normalize_quote("we ship on Fri"), msg)
    assert not quotes.occurs_in(quotes.normalize_quote("ecided we ship on"), msg)
    assert quotes.occurs_in(quotes.normalize_quote("周五发布"), quotes.normalize("我们周五发布吧"))


def test_occurs_in_does_not_cut_joined_words():
    def found(quote, message):
        return quotes.occurs_in(quotes.normalize_quote(quote), quotes.normalize(message))
    assert not found("rename the package to atlas", "yes, rename the package to atlas-core")
    assert not found("freeze it until version 2", "freeze it until version 2.0")
    assert not found("core is the new name", "atlas-core is the new name")
    assert not found("we won", "we won't ship it")
    assert found("rename the package to atlas-core", "yes, rename the package to atlas-core")
    assert found("ship it on Friday", "ok - ship it on Friday. thanks")
    assert not found("ship it on Friday", "ship it on Friday/Saturday? no idea")


def test_quote_size():
    assert quotes.quote_size("ship it now please") == 4
    assert quotes.quote_size("yes") == 1
    assert quotes.quote_size("周五发布") == 4
    assert quotes.quote_size("好的") == 2
    assert quotes.quote_size("用 uv 吧") == 3


def _messages(*texts):
    return quotes.Messages([{"text": t, "msg_id": str(i)} for i, t in enumerate(texts)])


def test_messages_find_long_quote():
    assert _messages("ok", "I decided we ship on Friday.").find("we ship on Friday")[0] == "found"
    assert _messages("I decided we ship on Friday.").find("we ship on Monday")[0] == "not_found"


def test_messages_find_whole_only():
    assert _messages("Yes!").find("yes", whole_only=True)[0] == "found"
    assert _messages("好").find("好", whole_only=True)[0] == "found"
    assert _messages("yes, but only after the tests pass").find("yes", whole_only=True)[0] == "inside"
    assert _messages("no").find("yes", whole_only=True)[0] == "not_found"


def test_messages_prefer_first_row():
    status, row = _messages("ship on Friday now", "ship on Friday now").find("ship on Friday now")
    assert status == "found" and row["msg_id"] == "0"


def test_content_tokens():
    assert quotes.content_tokens("I decided we ship on Friday") == {"decided", "ship", "friday"}
    assert quotes.content_tokens("The user approved it, yes") == {"approved"}
    assert quotes.content_tokens("就周五发布吧") == {"就周", "周五", "五发", "发布", "布吧"}
    assert "用户" not in quotes.content_tokens("用户决定")


def test_related():
    assert quotes.related("I decided we ship on Friday", 'User decided to ship on Friday ( Q )')
    assert not quotes.related("can you look at the logs for me", "The user approved deleting the prod database ( Q )")
    assert quotes.related("就周五发布吧", "用户决定周五发布（用户原话： Q ）。")
    assert not quotes.related("ship it", "User decided to ship ( Q )")  # one shared token is not enough
