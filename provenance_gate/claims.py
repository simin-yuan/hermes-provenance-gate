"""Split written text into sentences and find the ones that state a user decision."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from . import patterns
from .quotes import mask_quotes

SCOPE_DECISIONS = "decisions"
SCOPE_WITH_PREFERENCES = "decisions+preferences"
SCOPES = (SCOPE_DECISIONS, SCOPE_WITH_PREFERENCES)

_LINE_MARKER = re.compile(r"^\s*(?:(?:[-*+]|\d+[.)]|#{1,6}|>)\s+)*")
_ASCII_ENDS = ".!?;"
_CJK_ENDS = "。！？；"
_QUOTE_PAIRS = {"“": "”", "「": "」", "『": "』"}
_WORD = re.compile(r"[A-Za-z]+")


@dataclass(frozen=True)
class Claim:
    sentence: str
    pattern: str
    kind: str  # "decision" | "preference"


def _split_segment(segment: str) -> list[str]:
    """Split on sentence ends that are not inside a quotation."""
    sentences, start, closers, in_straight = [], 0, [], False
    n = len(segment)
    for i, ch in enumerate(segment):
        if ch == '"':
            in_straight = not in_straight
        elif ch in _QUOTE_PAIRS:
            closers.append(_QUOTE_PAIRS[ch])
        elif closers and ch == closers[-1]:
            closers.pop()
        if in_straight or closers:
            continue
        if ch in _CJK_ENDS or (ch in _ASCII_ENDS and (i + 1 == n or segment[i + 1].isspace())):
            sentences.append(segment[start:i + 1])
            start = i + 1
    sentences.append(segment[start:])
    return [s.strip() for s in sentences if s.strip()]


def split_sentences(text: str) -> list[str]:
    """Lines first (CRLF and CR count as line ends), then sentences. Markdown list, heading and
    blockquote markers are removed; a table row is split into its cells."""
    out: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _LINE_MARKER.sub("", line).strip()
        if not line:
            continue
        cells = [c for c in line.strip("|").split("|")] if line.startswith("|") else [line]
        for cell in cells:
            out += _split_segment(cell.strip())
    return out


def _verbs(scope: str) -> list[tuple[str, str, str, str]]:
    """``(lang, kind, name, verb_regex)`` for the scope."""
    rows = [("en", "decision", k, v[0]) for k, v in patterns.EN_DECISION_VERBS.items()]
    rows += [("zh", "decision", k, v[0]) for k, v in patterns.ZH_DECISION_VERBS.items()]
    if scope == SCOPE_WITH_PREFERENCES:
        rows += [("en", "preference", k, v[0]) for k, v in patterns.EN_PREFERENCE_VERBS.items()]
        rows += [("zh", "preference", k, v[0]) for k, v in patterns.ZH_PREFERENCE_VERBS.items()]
    return rows


@lru_cache(maxsize=4)
def _compiled(scope: str) -> list[tuple[str, str, str, re.Pattern]]:
    out = []
    for lang, kind, name, verb in _verbs(scope):
        if lang == "en":
            regex = (rf"(?<![\w-])(?P<subj>{patterns.EN_SUBJECT}){patterns.EN_AUX}{patterns.EN_ADVERBS}"
                     rf"\s+(?:{verb})(?![\w-])")
            out.append((lang, kind, name, re.compile(regex, re.IGNORECASE)))
        else:
            regex = rf"(?P<subj>{patterns.ZH_SUBJECT}){patterns.ZH_ADVERBS}(?:{verb})"
            out.append((lang, kind, name, re.compile(regex)))
    return out


def _en_subject_ok(masked: str, match: re.Match) -> bool:
    before = masked[:match.start()]
    words = [w.lower() for w in _WORD.findall(before)[-3:]]
    if any(w in patterns.EN_OPEN_BEFORE for w in words):
        return False
    if match.group("subj").lower().startswith("the"):
        return True
    stripped = before.rstrip(" \t*_")
    if not stripped:
        return True
    if stripped[-1] in ",;:(—–-|>\"'“”":
        return True
    last = _WORD.findall(stripped)
    return bool(last) and stripped.lower().endswith(last[-1].lower()) and \
        last[-1].lower() in patterns.EN_BARE_SUBJECT_AFTER


def _zh_subject_ok(masked: str, match: re.Match) -> bool:
    before = masked[:match.start()].rstrip()
    if before.endswith(patterns.ZH_OPEN_BEFORE):
        return False
    after = masked[match.end():].lstrip()
    if after.startswith("了"):
        after = after[1:]
    return not after.startswith(patterns.ZH_OPEN_AFTER)


def _is_question(masked: str) -> bool:
    return masked.rstrip(" \t)]）】*_\"'”’").endswith(("?", "？"))


def find_claim(sentence: str, scope: str = SCOPE_DECISIONS) -> Claim | None:
    masked = mask_quotes(sentence)
    if _is_question(masked):
        return None
    for lang, kind, name, regex in _compiled(scope):
        for match in regex.finditer(masked):
            ok = _en_subject_ok(masked, match) if lang == "en" else _zh_subject_ok(masked, match)
            if ok:
                return Claim(sentence=sentence, pattern=name, kind=kind)
    return None


def find_claims(text: str, scope: str = SCOPE_DECISIONS) -> list[Claim]:
    return [claim for sentence in split_sentences(text) if (claim := find_claim(sentence, scope))]
