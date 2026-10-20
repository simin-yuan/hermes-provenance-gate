"""Quoted spans in written text, verbatim matching against what the user typed, and the content
tokens that tie a quote to its claim.

Quote styles: "…", “…”, '…' and ‘…’ (single quotes only around two or more words), 「…」, 『…』.
Matching NFKC-normalizes both sides, collapses whitespace, case-folds, treats straight and curly
quotes alike and ignores trailing punctuation of the quote. Nothing else is forgiven: no fuzzy
or partial-word matching. Every scan here is linear in the length of the text.
"""

from __future__ import annotations

import bisect
import re
import unicodedata
from functools import lru_cache

from . import patterns

_DOUBLE_QUOTES = '"“”„'
_SINGLE_OPEN = "'‘"
_SINGLE_CLOSE = "'’"
_CORNERS = (("「", "」"), ("『", "』"))

_QUOTE_FOLD = str.maketrans({"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'"})
_TRAILING_PUNCT = re.compile(r"[\s.,!?;:。，！？；：、…]+$")
_WS = re.compile(r"\s+")
# Unicode Default_Ignorable_Code_Point (DerivedCoreProperties.txt): invisible characters that only
# ever hide words (soft hyphen, zero-width and joiner characters, bidi controls, variation
# selectors, invisible operators U+2061-2064, Mongolian FVS/U+180E, combining grapheme joiner
# U+034F, Hangul fillers, tag characters, the BOM ...). All are removed before detection.
_DEFAULT_IGNORABLE_RANGES = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
    (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164),
    (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF),
)
_INVISIBLE = {code: None for lo, hi in _DEFAULT_IGNORABLE_RANGES for code in range(lo, hi + 1)}
_LATIN_WORD = re.compile(r"[^\W\d_]+")
_SPACE_INSIDE = re.compile(r"\S\s+\S")

SHORT_QUOTE_LIMIT = 4  # words (Latin) or characters (CJK)


def is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (0x3400 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF or 0x3040 <= code <= 0x30FF
            or 0xAC00 <= code <= 0xD7AF or 0x20000 <= code <= 0x2FA1F)


def _word_char(ch: str) -> bool:
    return ch.isalnum() and not is_cjk(ch)


def detection_form(text: str) -> str:
    """Text as claim detection sees it: NFKC (folds full-width letters) without invisible characters."""
    return unicodedata.normalize("NFKC", text).translate(_INVISIBLE)


# --- spans ---------------------------------------------------------------------------------

_DOUBLE_RE = re.compile('["“”„]')
_SINGLE_RE = re.compile("['‘’]")
_NEWLINE_RE = re.compile("\n")
_CJK_RE = re.compile("[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\uf900-\ufaff\U00020000-\U0002fa1f]+")


def _double_spans(text: str, newlines: list[int]) -> list[tuple[int, int, str]]:
    positions = [m.start() for m in _DOUBLE_RE.finditer(text)]
    spans, k = [], 0
    while k + 1 < len(positions):
        a, b = positions[k], positions[k + 1]
        if b > a + 1 and not _has_newline(newlines, a, b):
            spans.append((a, b + 1, text[a + 1:b]))
            k += 2
        else:
            k += 1
    return spans


def _single_spans(text: str, newlines: list[int]) -> list[tuple[int, int, str]]:
    """Pairs of single quotes that are not apostrophes inside a word, around two or more words."""
    n = len(text)
    positions = [i for i in (m.start() for m in _SINGLE_RE.finditer(text))
                 if not (0 < i < n - 1 and _word_char(text[i - 1]) and _word_char(text[i + 1]))]
    spans, k = [], 0
    while k + 1 < len(positions):
        a, b = positions[k], positions[k + 1]
        opener_ok = text[a] in _SINGLE_OPEN and (a == 0 or not _word_char(text[a - 1]))
        closer_ok = text[b] in _SINGLE_CLOSE and (b + 1 == n or not _word_char(text[b + 1]))
        inner = text[a + 1:b]
        if opener_ok and closer_ok and not _has_newline(newlines, a, b) and _SPACE_INSIDE.search(inner.strip()):
            spans.append((a, b + 1, inner))
            k += 2
        else:
            k += 1
    return spans


def _corner_spans(text: str) -> list[tuple[int, int, str]]:
    spans = []
    for opener, closer in _CORNERS:
        start = text.find(opener)
        while start != -1:
            end = text.find(closer, start + 1)
            if end == -1:
                break
            if end > start + 1:
                spans.append((start, end + 1, text[start + 1:end]))
            start = text.find(opener, end + 1)
    return spans


def _has_newline(newlines: list[int], a: int, b: int) -> bool:
    i = bisect.bisect_right(newlines, a)
    return i < len(newlines) and newlines[i] < b


def quote_spans(text: str) -> list[tuple[int, int, str]]:
    """``(start, end, inner_text)`` for every quoted span, in order; overlapping spans are dropped."""
    newlines = [m.start() for m in _NEWLINE_RE.finditer(text)]
    found = _double_spans(text, newlines) + _single_spans(text, newlines) + _corner_spans(text)
    spans: list[tuple[int, int, str]] = []
    for span in sorted(found):
        if not spans or span[0] >= spans[-1][1]:
            if span[2].strip():
                spans.append(span)
    return spans


def extract_quotes(text: str) -> list[str]:
    return [inner for _start, _end, inner in quote_spans(text)]


def remove_spans(text: str, spans: list[tuple[int, int, str]]) -> str:
    """``text`` with ``spans`` replaced by a neutral placeholder word."""
    out, last = [], 0
    for start, end, _inner in sorted(spans):
        out += [text[last:start], " Q "]
        last = end
    out.append(text[last:])
    return "".join(out)


# --- matching ------------------------------------------------------------------------------

@lru_cache(maxsize=65536)
def normalize(text: str) -> str:
    """Matching form (cached: captured messages are normalized again on every check)."""
    text = detection_form(text).translate(_QUOTE_FOLD)
    return _WS.sub(" ", text).strip().casefold()


def normalize_quote(text: str) -> str:
    return _TRAILING_PUNCT.sub("", normalize(text))


def quote_size(text: str) -> int:
    """CJK characters plus Latin words (a pure-Latin quote counts words, a pure-CJK one characters)."""
    cjk = sum(1 for ch in text if is_cjk(ch))
    latin = "".join(" " if is_cjk(ch) else ch for ch in text)
    return cjk + len(re.findall(r"[^\W_]+", latin))


def is_short(quote: str) -> bool:
    return quote_size(quote) < SHORT_QUOTE_LIMIT


_JOINERS = "-_./'"


def _word_continues(text: str, index: int, step: int) -> bool:
    """The Latin word at the edge of a match continues at ``text[index]`` (directly, or across a
    joiner such as ``atlas-core``, ``v2.0`` or ``don't``)."""
    if not 0 <= index < len(text):
        return False
    if _word_char(text[index]):
        return True
    nxt = index + step
    return text[index] in _JOINERS and 0 <= nxt < len(text) and _word_char(text[nxt])


def occurrences(quote_norm: str, message_norm: str) -> list[int]:
    """Start offsets where ``quote_norm`` occurs in ``message_norm`` without cutting a Latin word."""
    found: list[int] = []
    if not quote_norm:
        return found
    start = message_norm.find(quote_norm)
    while start != -1:
        end = start + len(quote_norm)
        cut_left = _word_char(quote_norm[0]) and _word_continues(message_norm, start - 1, -1)
        cut_right = _word_char(quote_norm[-1]) and _word_continues(message_norm, end, 1)
        if not cut_left and not cut_right:
            found.append(start)
        start = message_norm.find(quote_norm, start + 1)
    return found


def occurs_in(quote_norm: str, message_norm: str) -> bool:
    """``quote_norm`` occurs in ``message_norm`` without cutting a Latin word in half."""
    return bool(occurrences(quote_norm, message_norm))


_NEGATION_WORDS = frozenset(patterns.EN_NEGATIONS)
_NEGATION_LOOKBACK = 12


def has_negation(text_norm: str) -> bool:
    if any(ch in patterns.ZH_NEGATION_CHARS for ch in text_norm):
        return True
    return any(w in _NEGATION_WORDS or w.endswith(("n't", "n’t"))
               for w in re.findall(r"[a-z]+(?:['’][a-z]+)?", text_norm))


_SENTENCE_END = re.compile(r"[.!?;。！？；\n]")


def negated_at(message_norm: str, start: int) -> bool:
    """A negation word earlier in the same sentence of the user's message, before ``start`` (up to
    12 words or 6 CJK characters back): "Never delete the prod database" and "Do not, under any
    circumstances, delete the prod database" do not back a quote of "delete the prod database"."""
    window = message_norm[max(0, start - 200):start]
    ends = list(_SENTENCE_END.finditer(window))
    before = window[ends[-1].end():] if ends else window
    cjk_tail = before.rstrip()[-6:]
    if any(ch in patterns.ZH_NEGATION_CHARS for ch in cjk_tail):
        return True
    words = re.findall(r"[a-z]+(?:['’][a-z]+)?", before)[-_NEGATION_LOOKBACK:]
    return any(w in _NEGATION_WORDS or w.endswith(("n't", "n’t")) for w in words)


def leads_with_negation(quote: str) -> bool:
    """The quote's first few words (or characters, for CJK) hold a negation: "don't approve …"."""
    norm = normalize(quote)
    words = re.findall(r"[a-z]+(?:['’][a-z]+)?", norm)[:patterns.QUOTE_LEADING_WORDS]
    if any(w in _NEGATION_WORDS or w.endswith(("n't", "n’t")) for w in words):
        return True
    return any(ch in patterns.ZH_NEGATION_CHARS for ch in norm[:4])


def in_question(message_norm: str, end: int) -> bool:
    """The sentence holding the quoted span ends with a question mark: "Should we delete the prod
    database?" asks; it does not back "approved deleting the prod database"."""
    match = _SENTENCE_END.search(message_norm, end)
    tail = message_norm[end:match.start() + 1] if match else message_norm[end:]
    return tail.rstrip().endswith(("?", "？"))


def equals_whole(quote_norm: str, message_norm_quote: str) -> bool:
    return bool(quote_norm) and quote_norm == message_norm_quote


class Messages:
    """Captured user messages prepared once per check: newest first, normalized once."""

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.norm = [normalize(row.get("text") or "") for row in rows]
        self.norm_whole = [_TRAILING_PUNCT.sub("", n) for n in self.norm]

    def find(self, quote: str, whole_only: bool = False) -> tuple[str, dict | None]:
        """``("found", row)``, ``("inside", row)`` (a short quote inside a longer message),
        ``("negated", row)`` (every occurrence follows a negation word), ``("question", row)`` (every
        occurrence is inside a question) or ``("not_found", None)``.
        ``whole_only`` applies the short-quote rule."""
        q = normalize_quote(quote)
        inside = negated = question = None
        for i, row in enumerate(self.rows):
            if whole_only:
                if equals_whole(q, self.norm_whole[i]):
                    return "found", row
                if inside is None and occurs_in(q, self.norm[i]):
                    inside = row
                continue
            starts = [st for st in occurrences(q, self.norm[i]) if not in_question(self.norm[i], st + len(q))]
            if not starts and occurs_in(q, self.norm[i]) and question is None:
                question = row
            if starts and has_negation(q):
                return "found", row  # the quote carries its own negation; nothing was dropped
            if any(not negated_at(self.norm[i], start) for start in starts):
                return "found", row
            if starts and negated is None:
                negated = row
        if inside is not None:
            return "inside", inside
        if negated is not None:
            return "negated", negated
        return ("question", question) if question is not None else ("not_found", None)


# --- relatedness ---------------------------------------------------------------------------

def content_tokens(text: str) -> set[str]:
    """Latin words of 3+ letters (lower-cased, minus stopwords) and CJK character bigrams."""
    norm = normalize(text)
    tokens = {w for w in _LATIN_WORD.findall(_CJK_RE.sub(" ", norm))
              if len(w) >= 3 and w not in patterns.STOPWORDS}
    for run in _CJK_RE.findall(norm):
        tokens.update(bigram for bigram in (run[i:i + 2] for i in range(len(run) - 1))
                      if bigram not in patterns.ZH_STOP_BIGRAMS)
    return tokens


def related(quote: str, claim_without_quote: str) -> bool:
    return len(content_tokens(quote) & content_tokens(claim_without_quote)) >= patterns.MIN_SHARED_TOKENS
