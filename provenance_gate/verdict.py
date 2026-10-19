"""What a memory / skill_manage call adds, and whether each decision claim in it has evidence."""

from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from . import claims as claims_mod
from . import quotes, store
from .claims import Claim

GATED_TOOLS = ("memory", "skill_manage")
MODE_BLOCK, MODE_OBSERVE = "block", "observe"
RETRY_LIMIT = 3
CLAIM_PREVIEW = 160

REASON_NO_QUOTE = "no_quote"
REASON_NOT_FOUND = "quote_not_found"
REASON_TOO_SHORT = "quote_too_short"


def mode() -> str:
    value = os.environ.get("PROVENANCE_GATE_MODE", "").strip().lower()
    return MODE_OBSERVE if value == MODE_OBSERVE else MODE_BLOCK


def scope() -> str:
    value = os.environ.get("PROVENANCE_GATE_SCOPE", "").strip().lower().replace(" ", "")
    return value if value in claims_mod.SCOPES else claims_mod.SCOPE_DECISIONS


def _text(*values: Any) -> str:
    """The first non-empty string among ``values`` (the tools' own precedence)."""
    for value in values:
        if isinstance(value, str) and value:
            return value
    return ""


def added_texts(tool_name: str, args: Any) -> list[tuple[str, str]]:
    """``(target, text)`` for every piece of text the call would add. Removals add nothing.

    memory (tools/memory_tool.py): ``add`` / ``patch`` write ``content`` (alias ``new_text``),
    as a single op or as each item of ``operations``. skill_manage (tools/skill_manager_tool.py):
    ``create`` -> ``content``; ``patch`` -> ``new_string`` or a full ``content``; ``edit`` (legacy)
    -> ``content``; ``write_file`` -> ``file_content``; as ``operations`` or the legacy flat shape.
    """
    if not isinstance(args, dict):
        return []
    out: list[tuple[str, str]] = []
    if tool_name == "memory":
        target = args.get("target") if isinstance(args.get("target"), str) else "memory"
        ops = args.get("operations")
        ops = ops if isinstance(ops, list) else [args]
        for op in ops:
            if isinstance(op, dict) and op.get("action") in ("add", "patch"):
                text = _text(op.get("content"), op.get("new_text"))
                if text:
                    out.append((f"memory:{target}", text))
    elif tool_name == "skill_manage":
        ops = args.get("operations")
        ops = ops if isinstance(ops, list) else [args]
        default_name = args.get("name") if isinstance(args.get("name"), str) else ""
        for op in ops:
            if not isinstance(op, dict):
                continue
            action = op.get("action")
            name = op.get("name") if isinstance(op.get("name"), str) else default_name
            if action in ("create", "edit"):
                texts = [op.get("content")]
            elif action == "patch":  # the tool refuses both at once; check whichever is present
                texts = [op.get("new_string"), op.get("content")]
            elif action == "write_file":
                texts = [op.get("file_content")]
            else:
                texts = []
            out += [(f"skill:{name}", text) for text in texts if isinstance(text, str) and text]
    return out


@dataclass
class ClaimCheck:
    target: str
    claim: Claim
    ok: bool
    reason: str | None = None
    quote: str | None = None
    msg_id: str | None = None
    matched_in: str | None = None


def check_claim(target: str, claim: Claim, session_id: str, said: list[dict]) -> ClaimCheck:
    """A claim passes when one of its quotes is found: in this session's messages first, then in
    every retained message (newest first)."""
    found_quotes = quotes.extract_quotes(claim.sentence)
    if not found_quotes:
        return ClaimCheck(target, claim, False, REASON_NO_QUOTE)
    newest_first = list(reversed(said))
    this = [row for row in newest_first if session_id and row.get("session_id") == session_id]
    other = [row for row in newest_first if not (session_id and row.get("session_id") == session_id)]
    failure = (REASON_NOT_FOUND, found_quotes[0])
    for quote in found_quotes:
        for where, rows in (("this-session", this), ("other-session", other)):
            result, row = quotes.match(quote, rows)
            if result == "found":
                return ClaimCheck(target, claim, True, None, quote, row.get("msg_id"), where)
            if result == "too_short" and failure[0] == REASON_NOT_FOUND:
                failure = (REASON_TOO_SHORT, quote)
    return ClaimCheck(target, claim, False, failure[0], failure[1])


def evaluate(tool_name: str, args: Any, session_id: str, said: list[dict],
             claim_scope: str = claims_mod.SCOPE_DECISIONS) -> list[ClaimCheck]:
    """One check per decision claim in the text the call adds (empty when there is none)."""
    checks = []
    for target, text in added_texts(tool_name, args):
        for claim in claims_mod.find_claims(text, claim_scope):
            checks.append(check_claim(target, claim, session_id, said))
    return checks


def _preview(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def block_message(failed: ClaimCheck, attempts: int) -> str:
    claim = _preview(failed.claim.sentence, CLAIM_PREVIEW)
    parts = [f'provenance-gate: this write states a user decision ("{claim}") without the user\'s words.']
    if failed.reason == REASON_NOT_FOUND:
        parts.append(f'The quoted words ("{_preview(failed.quote or "", 80)}") are not in anything the user said.')
    elif failed.reason == REASON_TOO_SHORT:
        parts.append(f'The quote ("{_preview(failed.quote or "", 80)}") is shorter than '
                     f"{quotes.SHORT_QUOTE_LIMIT} words and is not the user's whole message.")
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
           "msg_id": check.msg_id, "matched_in": check.matched_in, "verdict": verdict}
    if check.reason:
        row["reason"] = check.reason
    return row


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
