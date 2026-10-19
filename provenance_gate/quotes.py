"""Quoted spans in written text, and verbatim matching against what the user said.

Quote styles: "…", “…”, '…' and ‘…’ (single quotes only around two or more words), 「…」, 『…』.
Matching NFKC-normalizes both sides, collapses whitespace, case-folds, treats straight and curly
quotes alike and ignores trailing punctuation of the quote. Nothing else is forgiven: no fuzzy
or partial-word matching.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

_DOUBLE = re.compile(r'["“„]([^"“”„\n]+?)["”“]')
_SINGLE = re.compile(r"(?<!\w)['‘]((?:[^'‘’\n]|(?<=\w)['’](?=\w))+?)['’](?!\w)")
_CORNER = re.compile(r"「([^「」]+)」")
_WHITE_CORNER = re.compile(r"『([^『』]+)』")
_BACKTICK = re.compile(r"`[^`\n]*`")

_QUOTE_FOLD = str.maketrans({"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'"})
_TRAILING_PUNCT = re.compile(r"[\s.,!?;:。，！？；：、…]+$")
_WS = re.compile(r"\s+")

SHORT_QUOTE_LIMIT = 4  # words (Latin) or characters (CJK)


def is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (0x3400 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF or 0x3040 <= code <= 0x30FF
            or 0xAC00 <= code <= 0xD7AF or 0x20000 <= code <= 0x2FA1F)


def _word_char(ch: str) -> bool:
    return ch.isalnum() and not is_cjk(ch)


def quote_spans(text: str) -> list[tuple[int, int, str]]:
    """``(start, end, inner_text)`` for every quoted span, in order; overlapping spans are dropped."""
    found: list[tuple[int, int, str]] = []
    for pattern in (_DOUBLE, _CORNER, _WHITE_CORNER):
        found += [(m.start(), m.end(), m.group(1)) for m in pattern.finditer(text)]
    for m in _SINGLE.finditer(text):
        if len(m.group(1).split()) >= 2:
            found.append((m.start(), m.end(), m.group(1)))
    spans: list[tuple[int, int, str]] = []
    for span in sorted(found):
        if not spans or span[0] >= spans[-1][1]:
            spans.append(span)
    return spans


def extract_quotes(text: str) -> list[str]:
    return [inner for _start, _end, inner in quote_spans(text) if inner.strip()]


def mask_quotes(text: str) -> str:
    """``text`` with quoted spans and inline code replaced by a neutral placeholder."""
    out, last = [], 0
    for start, end, _inner in quote_spans(text):
        out += [text[last:start], " Q "]
        last = end
    out.append(text[last:])
    return _BACKTICK.sub(" Q ", "".join(out))


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_QUOTE_FOLD)
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


def occurs_in(quote_norm: str, message_norm: str) -> bool:
    """``quote_norm`` occurs in ``message_norm`` without cutting a Latin word in half."""
    if not quote_norm:
        return False
    start = message_norm.find(quote_norm)
    while start != -1:
        end = start + len(quote_norm)
        cut_left = _word_char(quote_norm[0]) and _word_continues(message_norm, start - 1, -1)
        cut_right = _word_char(quote_norm[-1]) and _word_continues(message_norm, end, 1)
        if not cut_left and not cut_right:
            return True
        start = message_norm.find(quote_norm, start + 1)
    return False


def equals_whole(quote_norm: str, message: str) -> bool:
    return bool(quote_norm) and quote_norm == normalize_quote(message)


def match(quote: str, messages: Iterable[dict]) -> tuple[str, dict | None]:
    """Look ``quote`` up in ``messages`` (newest first). Returns ``("found", row)``,
    ``("too_short", row_or_None)`` or ``("not_found", None)``."""
    q = normalize_quote(quote)
    short = is_short(quote)
    seen_inside = None
    for row in messages:
        text = row.get("text") or ""
        if short:
            if equals_whole(q, text):
                return "found", row
            if seen_inside is None and occurs_in(q, normalize(text)):
                seen_inside = row
        elif occurs_in(q, normalize(text)):
            return "found", row
    if short and seen_inside is not None:
        return "too_short", seen_inside
    return "not_found", None
