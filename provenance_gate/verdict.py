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
from . import quotes, store
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
REASON_TOO_MANY = "too_many_claims"
REASON_UNREADABLE = "unreadable_arguments"
REASON_INTERNAL = "internal_error"
_REASON_ORDER = (REASON_UNRELATED, REASON_TOO_SHORT, REASON_NOT_FOUND)


def mode() -> str:
    value = os.environ.get("PROVENANCE_GATE_MODE", "").strip().lower()
    return MODE_OBSERVE if value == MODE_OBSERVE else MODE_BLOCK


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

    def __init__(self, said: list[dict], session_id: str, search: str):
        mine = [row for row in said if session_id and row.get("session_id") == session_id]
        self.session = quotes.Messages(list(reversed(mine)))
        self.recent = quotes.Messages(list(reversed(mine[-RECENT_FOR_SHORT_QUOTES:])))
        others = [row for row in said if not (session_id and row.get("session_id") == session_id)]
        self.others = quotes.Messages(list(reversed(others))) if search == SEARCH_PROFILE else None
        self._cache: dict[str, tuple[str, dict | None, str | None]] = {}

    def lookup(self, quote: str) -> tuple[str, dict | None, str | None]:
        """``(status, row, where)``; status is "found", "too_short" or "not_found"."""
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
        if self.others is not None:
            status, row = self.others.find(quote)
            if status == "found":
                return "found", row, "other-session"
        return "not_found", None, None


def check_sentence(target: str, sentence: str, claim_scope: str, evidence: Evidence) -> ClaimCheck | None:
    """None when ``sentence`` is not a claim; otherwise its check."""
    if claims_mod.find_claim(sentence, claim_scope) is None:
        return None
    spans = quotes.quote_spans(sentence)[:MAX_SPANS_PER_SENTENCE]
    looked = [(span, *evidence.lookup(span[2])) for span in spans]
    verified = [span for span, status, _row, _where in looked if status == "found"]
    claim = claims_mod.find_claim(quotes.remove_spans(sentence, verified), claim_scope)
    if claim is None:
        return None  # the decision words were inside the user's own quoted words
    claim = Claim(sentence=sentence, pattern=claim.pattern, kind=claim.kind)
    if not spans:
        return ClaimCheck(target, claim, False, REASON_NO_QUOTE)
    reasons: dict[str, str] = {}
    for span, status, row, where in looked:
        inner = span[2]
        if status == "found":
            if quotes.is_short(inner) or quotes.related(inner, quotes.remove_spans(sentence, [span])):
                return ClaimCheck(target, claim, True, None, inner, row.get("msg_id"), where, row.get("sender_id") or "")
            reasons.setdefault(REASON_UNRELATED, inner)
        elif status == "too_short":
            reasons.setdefault(REASON_TOO_SHORT, inner)
        else:
            reasons.setdefault(REASON_NOT_FOUND, inner)
    reason = next(r for r in _REASON_ORDER if r in reasons)
    return ClaimCheck(target, claim, False, reason, reasons[reason])


def evaluate(tool_name: str, args: Any, session_id: str, said: list[dict],
             claim_scope: str = claims_mod.SCOPE_DECISIONS, search: str = SEARCH_SESSION) -> list[ClaimCheck]:
    """One check per decision claim in the text the call adds (empty when there is none).
    Raises ``ArgError`` when the arguments cannot be read."""
    texts = added_texts(tool_name, args)
    if not texts:
        return []
    evidence = Evidence(said, session_id, search)
    checks: list[ClaimCheck] = []
    for target, text in texts:
        text = quotes.detection_form(text)
        if not claims_mod.SUBJECT_HINT.search(text):
            continue  # no subject word anywhere: no claim is possible
        for sentence in claims_mod.split_sentences(text):
            if sentence.context != PROSE:
                continue
            check = check_sentence(target, sentence.text, claim_scope, evidence)
            if check is None:
                continue
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
