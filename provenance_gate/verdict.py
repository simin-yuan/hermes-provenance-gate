"""Whether each decision claim in a memory / skill_manage write has evidence.

For every prose sentence of the text a call adds:

1. Detect a claim on the sentence as written (quote marks are ordinary text).
2. Look up every quoted span in what the user typed. Spans that verify are removed and the claim
   is detected again: words inside a verified quote are the user's, not a claim. A span that does
   not verify stays ordinary text, so a claim hidden in quote marks (or between two inch marks) is
   still seen.
3. The claim passes when a verified quote shares at least two content tokens with the rest of the
   sentence (a short quote that is the user's whole recent message needs no shared tokens).
"""

from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from . import claims as claims_mod
from . import patterns, quotes, store
from .args import ArgError, added_texts
from .claims import PROSE, Claim

GATED_TOOLS = ("memory", "skill_manage")
MODE_BLOCK, MODE_OBSERVE = "block", "observe"
SEARCH_SESSION, SEARCH_PROFILE = "session", "profile"
RETRY_LIMIT = 3
CLAIM_PREVIEW = 160
RECENT_FOR_SHORT_QUOTES = 3
MAX_SPANS_PER_SENTENCE = 32
MAX_CLAIMS_PER_CALL = 100

REASON_NO_QUOTE = "no_quote"
REASON_NOT_FOUND = "quote_not_found"
REASON_TOO_SHORT = "quote_too_short"
REASON_UNRELATED = "quote_unrelated"
REASON_NEGATED = "quote_negated"
REASON_QUESTION = "quote_from_question"
REASON_TOO_MANY = "too_many_claims"
REASON_UNREADABLE = "unreadable_arguments"
REASON_INTERNAL = "internal_error"
_REASON_ORDER = (REASON_NEGATED, REASON_QUESTION, REASON_UNRELATED, REASON_TOO_SHORT, REASON_NOT_FOUND)


def mode() -> str:
    """``observe`` (the default: flag and log, never refuse) or ``block`` (opt-in)."""
    value = os.environ.get("PROVENANCE_GATE_MODE", "").strip().lower()
    return MODE_BLOCK if value == MODE_BLOCK else MODE_OBSERVE


def scope() -> str:
    value = os.environ.get("PROVENANCE_GATE_SCOPE", "").strip().lower().replace(" ", "")
    return value if value in claims_mod.SCOPES else claims_mod.SCOPE_DECISIONS


def search_mode() -> str:
    value = os.environ.get("PROVENANCE_GATE_SEARCH", "").strip().lower()
    return SEARCH_PROFILE if value == SEARCH_PROFILE else SEARCH_SESSION


@dataclass
class ClaimCheck:
    target: str
    claim: Claim
    ok: bool
    reason: str | None = None
    quote: str | None = None
    msg_id: str | None = None
    matched_in: str | None = None
    sender_id: str | None = None


class Evidence:
    """The captured messages a quote may come from, prepared once per call."""

    MAX_CHAIN = 32

    @classmethod
    def session_chain(cls, said: list[dict], session_id: str) -> set[str]:
        """``session_id`` and the sessions it continues after context compression (each row may
        carry the ``parent_session_id`` that pre_llm_call reported)."""
        parents = {row.get("session_id"): row.get("parent_session_id") for row in said if row.get("parent_session_id")}
        chain, current = set(), session_id
        while current and current not in chain and len(chain) < cls.MAX_CHAIN:
            chain.add(current)
            current = parents.get(current)
        return chain

    def __init__(self, said: list[dict], session_id: str, search: str):
        chain = self.session_chain(said, session_id) if session_id else set()
        said = [row for row in said if row.get("text")]
        mine = [row for row in said if row.get("session_id") in chain]
        self.session = quotes.Messages(list(reversed(mine)))
        self.recent = quotes.Messages(list(reversed(mine[-RECENT_FOR_SHORT_QUOTES:])))
        others = [row for row in said if row.get("session_id") not in chain]
        self.others = quotes.Messages(list(reversed(others))) if search == SEARCH_PROFILE else None
        self._cache: dict[str, tuple[str, dict | None, str | None]] = {}

    def lookup(self, quote: str) -> tuple[str, dict | None, str | None]:
        """``(status, row, where)``; status is "found", "too_short", "negated" or "not_found"."""
        if quote not in self._cache:
            self._cache[quote] = self._lookup(quote)
        return self._cache[quote]

    def _lookup(self, quote: str) -> tuple[str, dict | None, str | None]:
        if quotes.is_short(quote):
            # Short quotes count only as the user's whole message, in this session, among the
            # last few messages (a bare "yes" from an older turn says nothing about this claim).
            status, row = self.recent.find(quote, whole_only=True)
            if status == "found":
                return "found", row, "this-session"
            for pool in (self.session, self.others):
                if pool is not None and pool.find(quote, whole_only=True)[0] != "not_found":
                    return "too_short", None, None
            return "not_found", None, None
        status, row = self.session.find(quote)
        if status == "found":
            return "found", row, "this-session"
        seen = {status}
        if self.others is not None:
            other_status, other_row = self.others.find(quote)
            if other_status == "found":
                return "found", other_row, "other-session"
            seen.add(other_status)
        for kind in ("negated", "question"):
            if kind in seen:
                return kind, None, None
        return "not_found", None, None


def _mask(sentence: str, spans: list[tuple[int, int, str]]) -> str:
    """``sentence`` with ``spans`` blanked out, keeping every offset."""
    chars = list(sentence)
    for start, end, _inner in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def check_sentence(target: str, sentence: str, claim_scope: str, evidence: Evidence,
                   carried: frozenset[str] = frozenset()) -> list[ClaimCheck]:
    """One check per decision claim in ``sentence`` (empty when there is none).

    Each claim match owns the stretch of the sentence from its start to the next claim's start (the
    first one also owns everything before it), and needs a verified, related quote inside that
    stretch: "User decided X ("…") and the user approved Y." has two claims and one quote, so the
    second claim fails."""
    if not claims_mod.find_claim_matches(sentence, claim_scope):
        return []
    spans = quotes.quote_spans(sentence)[:MAX_SPANS_PER_SENTENCE]
    looked = []
    for span in spans:
        status, row, where = evidence.lookup(span[2])
        if status != "found" and quotes.normalize_quote(span[2]) in carried:
            # The quote is already in the stored entry or file this write edits: it was there before
            # this call, so an edit that keeps it does not need it in the current session.
            status, row, where = "found", {}, "stored-text"
        looked.append((span, status, row, where))
    verified = [span for span, status, _row, _where in looked if status == "found"]
    matches = claims_mod.find_claim_matches(_mask(sentence, verified), claim_scope)
    if not matches:
        return []  # the decision words were inside the user's own quoted words
    out: list[ClaimCheck] = []
    matches = matches[:MAX_CLAIMS_PER_CALL + 1]  # evaluate() stops at the cap anyway
    for index, (start, found) in enumerate(matches):
        lo = 0 if index == 0 else start
        hi = matches[index + 1][0] if index + 1 < len(matches) else len(sentence)
        claim = Claim(sentence=sentence, pattern=found.pattern, kind=found.kind)
        mine = [item for item in looked if lo <= item[0][0] < hi]
        if not mine:
            out.append(ClaimCheck(target, claim, False, REASON_NO_QUOTE))
            continue
        segment = sentence[lo:hi]
        reasons: dict[str, str] = {}
        passed = None
        for span, status, row, where in mine:
            inner = span[2]
            if status == "found":
                rest = quotes.remove_spans(segment, [(span[0] - lo, span[1] - lo, inner)])
                if (found.pattern in patterns.APPROVAL_PATTERNS and quotes.leads_with_negation(inner)
                        and not quotes.has_negation(quotes.normalize(rest))):
                    reasons.setdefault(REASON_NEGATED, inner)  # "approved X" backed by "don't approve X"
                    continue
                if quotes.is_short(inner) or quotes.related(inner, rest):
                    passed = ClaimCheck(target, claim, True, None, inner, row.get("msg_id"), where,
                                        row.get("sender_id") or "")
                    break
                reasons.setdefault(REASON_UNRELATED, inner)
            elif status == "too_short":
                reasons.setdefault(REASON_TOO_SHORT, inner)
            elif status == "negated":
                reasons.setdefault(REASON_NEGATED, inner)
            elif status == "question":
                reasons.setdefault(REASON_QUESTION, inner)
            else:
                reasons.setdefault(REASON_NOT_FOUND, inner)
        if passed is not None:
            out.append(passed)
            continue
        reason = next(r for r in _REASON_ORDER if r in reasons)
        out.append(ClaimCheck(target, claim, False, reason, reasons[reason]))
    return out


def evaluate(tool_name: str, args: Any, session_id: str, said: list[dict],
             claim_scope: str = claims_mod.SCOPE_DECISIONS, search: str = SEARCH_SESSION,
             resolver=None) -> list[ClaimCheck]:
    """One check per decision claim in the text the call adds (empty when there is none).
    Raises ``ArgError`` when the arguments cannot be read."""
    texts = added_texts(tool_name, args, resolver)
    if not texts:
        return []
    evidence = Evidence(said, session_id, search)
    checks: list[ClaimCheck] = []
    for target, text, before in texts:
        text = quotes.detection_form(text)
        if not claims_mod.SUBJECT_HINT.search(text):
            continue  # no subject word anywhere: no claim is possible
        stored = {quotes.normalize(s.text) for s in claims_mod.split_sentences(quotes.detection_form(before))} if before else set()
        carried = frozenset(quotes.normalize_quote(q) for q in quotes.extract_quotes(quotes.detection_form(before))) \
            if before else frozenset()
        for sentence in claims_mod.split_sentences(text):
            if sentence.context != PROSE:
                continue
            if stored and quotes.normalize(sentence.text) in stored:
                continue  # already in the stored text, unchanged: not a new claim (results.py)
            for check in check_sentence(target, sentence.text, claim_scope, evidence, carried):
                if len(checks) >= MAX_CLAIMS_PER_CALL:
                    return checks + [ClaimCheck(target, check.claim, False, REASON_TOO_MANY)]
                checks.append(check)
    return checks


def _preview(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def block_message(failed: ClaimCheck, attempts: int, search: str = SEARCH_SESSION) -> str:
    if failed.reason == REASON_UNREADABLE:
        return (f"provenance-gate: this write could not be checked because its arguments could not be read "
                f"({failed.quote}). Send operations as a JSON array of objects with string fields. Nothing was written.")
    if failed.reason == REASON_INTERNAL:
        return ("provenance-gate: this write could not be checked (internal error, logged). "
                "Nothing was written.")
    claim = _preview(failed.claim.sentence, CLAIM_PREVIEW)
    parts = [f'provenance-gate: this write states a user decision ("{claim}") without the user\'s words.']
    if failed.reason == REASON_NOT_FOUND:
        where = " in this session" if search == SEARCH_SESSION else ""
        parts.append(f'The quoted words ("{_preview(failed.quote or "", 80)}") are not in anything the user typed'
                     f"{where}.")
    elif failed.reason == REASON_TOO_SHORT:
        parts.append(f'The quote ("{_preview(failed.quote or "", 80)}") is shorter than '
                     f"{quotes.SHORT_QUOTE_LIMIT} words and is not one of the user's last "
                     f"{RECENT_FOR_SHORT_QUOTES} messages in full.")
    elif failed.reason == REASON_NEGATED:
        parts.append(f'The quoted words ("{_preview(failed.quote or "", 80)}") follow a negation in what the user '
                     "typed (for example \"never …\" or \"don't …\"), so they do not back the decision.")
    elif failed.reason == REASON_QUESTION:
        parts.append(f'The quoted words ("{_preview(failed.quote or "", 80)}") come from a question the user asked, '
                     "so they do not back the decision.")
    elif failed.reason == REASON_UNRELATED:
        parts.append(f'The quoted words ("{_preview(failed.quote or "", 80)}") do not share key words with '
                     "the decision.")
    elif failed.reason == REASON_TOO_MANY:
        parts.append(f"The write states more than {MAX_CLAIMS_PER_CALL} user decisions; split it.")
    parts.append('Quote the user\'s exact words in "..." if they said it, or write it as your own '
                 "suggestion or as an open question. Nothing was written.")
    if attempts >= RETRY_LIMIT:
        parts.append("Stop retrying this write; ask the user instead.")
    return " ".join(parts)


def session_hash(session_id: str) -> str:
    return hashlib.sha256((session_id or "").encode("utf-8")).hexdigest()[:12]


def evidence_row(check: ClaimCheck, tool_name: str, session_id: str, verdict: str, now: datetime) -> dict:
    row = {"ts": store.format_ts(now), "session": session_hash(session_id), "tool": tool_name,
           "target": check.target, "claim": check.claim.sentence, "quote": check.quote,
           "msg_id": check.msg_id, "matched_in": check.matched_in, "sender_id": check.sender_id,
           "verdict": verdict}
    if check.reason:
        row["reason"] = check.reason
    return row


def failure(tool_name: str, reason: str, detail: str) -> ClaimCheck:
    """A check standing for a call that could not be read or checked at all."""
    return ClaimCheck(tool_name, Claim(sentence="", pattern="", kind="decision"), False, reason, detail)


@dataclass
class BlockCounter:
    """Blocks per (session, turn, target); the third and later blocks add the stop line."""
    limit: int = 4096
    _counts: dict[tuple[str, str, str], int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def bump(self, session_id: str, turn_id: str, target: str) -> int:
        key = (session_id or "", turn_id or "", target)
        with self._lock:
            if key not in self._counts and len(self._counts) >= self.limit:
                self._counts.pop(next(iter(self._counts)))
            self._counts[key] = self._counts.get(key, 0) + 1
            return self._counts[key]


__all__ = ["ArgError", "ClaimCheck", "Evidence", "evaluate", "block_message", "evidence_row", "failure"]
