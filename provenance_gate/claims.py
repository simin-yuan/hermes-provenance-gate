"""Split written text into sentences and find the ones that state a user decision.

Detection works on ``quotes.detection_form`` text (NFKC, invisible characters removed). It uses one
combined regular expression per language, a bounded look-behind window and linear sentence
splitting, so checking is linear in the size of the write.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from . import patterns

SCOPE_DECISIONS = "decisions"
SCOPE_WITH_PREFERENCES = "decisions+preferences"
SCOPES = (SCOPE_DECISIONS, SCOPE_WITH_PREFERENCES)

PROSE, CODE, TABLE = "prose", "code", "table"

_LINE_MARKER = re.compile(r"^\s*(?:(?:[-*+]|\d+[.)]|#{1,6}|>)\s+)*")
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_INDENTED = re.compile(r"^(?: {4}|\t)")
_ASCII_ENDS = ".!?;"
_CJK_ENDS = "。！？；"
_QUOTE_PAIRS = {"“": "”", "「": "」", "『": "』"}
_CLOSERS = frozenset(_QUOTE_PAIRS.values())
# Quote marks, CJK sentence ends, and ASCII sentence ends followed by whitespace or the end.
_SPLIT_POINTS = re.compile(r'["“”「」『』。！？；]|[.!?;](?=\s|$)')
# Every claim pattern contains one of these; text without them cannot hold a claim.
SUBJECT_HINT = re.compile(r"user|owner|用户|用戶|主人|老板|老闆", re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z][A-Za-z'’.]*")
_WINDOW = 120  # characters before a match that the subject checks look at
_KEY_VALUE = re.compile(r"^\s*([^:=\n]{1,60}?)\s*(?::|=)(?=\s|$)")
_IDENTIFIER_KEY = re.compile(r"^[A-Za-z_][\w.\-/()\[\]]*$")
_SCALAR_VALUE = re.compile(r"^(?:true|false|yes|no|null|none|nil|~|on|off|-?\d+(?:\.\d+)?|\"[^\"]*\"|'[^']*'|\S{1,24})?$",
                           re.IGNORECASE)
_TRAILING_PAREN = re.compile(r"\s*\([^()]{0,80}\)\s*[.!]?\s*$")
_ZH_CLAUSE_END = re.compile(r"[，。；！？,;!?\n]")


@dataclass(frozen=True)
class Sentence:
    text: str
    context: str  # PROSE, CODE (fenced or indented) or TABLE (a Markdown table row)


@dataclass(frozen=True)
class Claim:
    sentence: str
    pattern: str
    kind: str  # "decision" | "preference"


def _split_segment(segment: str) -> list[str]:
    """Split on sentence ends that are not inside a quotation (linear; visits only quote marks
    and sentence ends)."""
    if not _SPLIT_POINTS.search(segment):
        return [segment] if segment.strip() else []
    sentences, start, closers, in_straight = [], 0, [], False
    for m in _SPLIT_POINTS.finditer(segment):
        ch, i = m.group(), m.start()
        if ch == '"':
            in_straight = not in_straight
            continue
        if ch in _QUOTE_PAIRS:
            closers.append(_QUOTE_PAIRS[ch])
            continue
        if closers and ch == closers[-1]:
            closers.pop()
            continue
        if in_straight or closers or ch in _CLOSERS:
            continue
        sentences.append(segment[start:i + 1])
        start = i + 1
    sentences.append(segment[start:])
    return [s.strip() for s in sentences if s.strip()]


def split_sentences(text: str) -> list[Sentence]:
    """Lines first (CRLF and CR count as line ends), then sentences. Lines inside a fenced code
    block or indented by four spaces / a tab are CODE; Markdown table rows are TABLE. Markdown list,
    heading and blockquote markers are removed from prose lines."""
    out: list[Sentence] = []
    in_fence = False
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _FENCE.match(raw):
            in_fence = not in_fence
            continue
        if not raw.strip():
            continue
        if in_fence or _INDENTED.match(raw):
            out.append(Sentence(raw.strip(), CODE))
            continue
        line = _LINE_MARKER.sub("", raw).strip()
        if line.startswith("|"):
            out.append(Sentence(line, TABLE))
            continue
        out += [Sentence(s, PROSE) for s in _split_segment(line)]
    return out


# --- compiled patterns -----------------------------------------------------------------------

def _verbs(table: dict, prefix: str) -> list[tuple[str, str, str]]:
    return [(f"{prefix}{i}", name, regex) for i, (name, (regex, _ex)) in enumerate(table.items())]


@lru_cache(maxsize=4)
def _compiled(scope: str):
    """``[(lang, kind, regex, {group: pattern name})]`` for the scope: one alternation per table."""
    tables = [("en", "decision", patterns.EN_DECISION_VERBS), ("zh", "decision", patterns.ZH_DECISION_VERBS)]
    if scope == SCOPE_WITH_PREFERENCES:
        tables += [("en", "preference", patterns.EN_PREFERENCE_VERBS),
                   ("zh", "preference", patterns.ZH_PREFERENCE_VERBS)]
    out = []
    for n, (lang, kind, table) in enumerate(tables):
        verbs = _verbs(table, f"g{n}_")
        alternation = "|".join(f"(?P<{group}>{regex})" for group, _name, regex in verbs)
        names = {group: name for group, name, _regex in verbs}
        if lang == "en":
            regex = re.compile(rf"(?<![\w-])(?P<subj>{patterns.EN_SUBJECT}){patterns.EN_AUX}{patterns.EN_ADVERBS}"
                               rf"\s+(?:{alternation})(?![\w-])", re.IGNORECASE)
        else:
            regex = re.compile(rf"(?P<subj>{patterns.ZH_SUBJECT}){patterns.ZH_ADVERBS}\s*(?:{alternation})")
        out.append((lang, kind, regex, names))
    phrases = _verbs(patterns.EN_DECISION_PHRASES, "p_")
    alternation = "|".join(f"(?P<{group}>{regex})" for group, _name, regex in phrases)
    out.append(("en-phrase", "decision", re.compile(alternation, re.IGNORECASE),
                {group: name for group, name, _regex in phrases}))
    return out


@lru_cache(maxsize=1)
def _spec_label_re():
    labels = "|".join(re.escape(label) for label in sorted(patterns.EN_SPEC_LABELS, key=len, reverse=True))
    return re.compile(rf"^\W*(?:{labels})\s*(?:#?\s*\d+[a-z]?)?\s*:", re.IGNORECASE)


# --- exemptions ------------------------------------------------------------------------------

def _words_before(text: str, index: int) -> list[str]:
    return [w.lower().rstrip(".'’") for w in _WORD.findall(text[max(0, index - _WINDOW):index])]


def _key_value_exempt(sentence: str, match_start: int) -> bool:
    """A key/value line describes data, not a decision: ``user confirmed: false`` (the claim is a
    short key whose value is a scalar) and spec lines with an identifier key (``on_confirm: user
    confirmed the modal``)."""
    kv = _KEY_VALUE.match(sentence)
    if not kv:
        return False
    key = kv.group(1).strip().strip("`*_")
    if _IDENTIFIER_KEY.match(key) and ("_" in key or "." in key or "(" in key):
        return True
    value = sentence[kv.end():].strip().rstrip(".;,")
    return (match_start < kv.end(1) and len(key.split()) <= patterns.KEY_MAX_WORDS
            and not any(ch in key for ch in ",—–") and bool(_SCALAR_VALUE.match(value)))


def _sentence_exempt(sentence: str) -> bool:
    lowered = sentence.lstrip(" *_>").lower()
    return bool(_spec_label_re().match(sentence)) or lowered.startswith(tuple(s + " " for s in patterns.EN_SPEC_STARTS))


def _en_subject_ok(text: str, match: re.Match, phrase: bool) -> bool:
    start = match.start()
    words = _words_before(text, start)
    if any(w in patterns.EN_CONDITIONALS for w in words[-3:]):
        return False
    if any(w in patterns.EN_GENERIC_CONTEXT for w in words):
        return False
    if _key_value_exempt(text, start):
        return False
    if phrase or match.group("subj").lower().startswith("the"):
        return True
    before = text[max(0, start - _WINDOW):start].rstrip(" \t*_")
    if not before:
        return True
    if before[-1] in ",;:(—–-|>\"'“”‘’":
        return True
    return bool(words) and before.lower().endswith(words[-1]) and words[-1] in patterns.EN_BARE_SUBJECT_AFTER


def _zh_subject_ok(text: str, match: re.Match) -> bool:
    before = text[max(0, match.start() - _WINDOW):match.start()]
    tight = before.rstrip()
    if tight.endswith(patterns.ZH_OPEN_BEFORE):
        return False
    if match.group("subj") in ("主人", "老板", "老闆", "用户", "用戶") and before.endswith(patterns.ZH_THIRD_PARTY_BEFORE):
        return False
    if any(marker in before for marker in patterns.ZH_GENERIC_CONTEXT):
        return False
    after = text[match.end():match.end() + _WINDOW].lstrip()
    if after.startswith(("了", "過", "过")):
        after = after[1:]
    for word, exclusions in patterns.ZH_OPEN_AFTER.items():
        if after.startswith(word):
            nxt = after[len(word):len(word) + 1]
            if not (nxt and nxt in exclusions):
                return False
    clause = _ZH_CLAUSE_END.split(text[match.end():match.end() + _WINDOW], 1)[0].rstrip()
    if clause.endswith(patterns.ZH_CLAUSE_FINAL_CONDITIONS):
        return False  # 用户确认收货后，… : the whole clause is a condition
    return not any(marker in clause for marker in patterns.ZH_CONDITION_MARKERS)


def _strip_tags(text: str) -> str:
    """Drop trailing parentheticals and tag questions (", ok?") so they do not make a question."""
    stripped = text.rstrip()
    for _ in range(3):
        new = _TRAILING_PAREN.sub("", stripped)
        lowered = new.lower()
        for tag in patterns.EN_TAG_QUESTIONS:
            for sep in (", ", ","):
                suffix = sep + tag + "?"
                if lowered.endswith(suffix):
                    new = new[: -len(suffix)] + "."
                    lowered = new.lower()
        if new == stripped:
            break
        stripped = new
    return stripped


def is_question(text: str) -> bool:
    return _strip_tags(text).rstrip(" \t)]）】*_\"'”’").endswith(("?", "？"))


def find_claim(sentence: str, scope: str = SCOPE_DECISIONS) -> Claim | None:
    """The first claim pattern in ``sentence`` (already in detection form) that passes the
    exemptions, or None. Quoted text is NOT masked here: the caller removes verified quotes."""
    if not sentence or not SUBJECT_HINT.search(sentence) or is_question(sentence) or _sentence_exempt(sentence):
        return None
    for lang, kind, regex, names in _compiled(scope):
        for match in regex.finditer(sentence):
            ok = (_zh_subject_ok(sentence, match) if lang == "zh"
                  else _en_subject_ok(sentence, match, phrase=lang == "en-phrase"))
            if ok:
                return Claim(sentence=sentence, pattern=names[match.lastgroup], kind=kind)
    return None


def find_claims(text: str, scope: str = SCOPE_DECISIONS) -> list[Claim]:
    """Claims in prose sentences, with no quote treated as evidence (a dry view used by tests)."""
    from .quotes import detection_form
    return [claim for s in split_sentences(detection_form(text))
            if s.context == PROSE and (claim := find_claim(s.text, scope))]
