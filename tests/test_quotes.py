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


def test_normalization():
    assert quotes.normalize("  Ship\tIt\n NOW ") == "ship it now"
    assert quotes.normalize("ＳＨＩＰ　ｉｔ") == "ship it"  # NFKC: full-width letters and space
    assert quotes.normalize("it’s “done”") == quotes.normalize("it's \"done\"")
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


def _rows(*texts):
    return [{"text": t, "msg_id": str(i)} for i, t in enumerate(texts)]


def test_match_long_quote():
    assert quotes.match("we ship on Friday", _rows("ok", "I decided we ship on Friday."))[0] == "found"
    assert quotes.match("we ship on Monday", _rows("I decided we ship on Friday."))[0] == "not_found"


def test_short_quote_must_equal_whole_message():
    assert quotes.match("yes", _rows("Yes!"))[0] == "found"
    assert quotes.match("好", _rows("好"))[0] == "found"
    assert quotes.match("go ahead", _rows("Go ahead."))[0] == "found"
    assert quotes.match("yes", _rows("yes, but only after the tests pass"))[0] == "too_short"
    assert quotes.match("好", _rows("好的，不过先跑测试"))[0] == "too_short"
    assert quotes.match("yes", _rows("no"))[0] == "not_found"


def test_match_prefers_first_row_given():
    status, row = quotes.match("ship on Friday now", _rows("ship on Friday now", "ship on Friday now"))
    assert status == "found" and row["msg_id"] == "0"
