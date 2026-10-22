"""Settling a condition that was written down and left open.

The write-time check answers one question: does the user's words back this claim? It cannot answer
the next one. ``only if the benchmark passes`` is a decision that depends on something that has not
happened yet, so the row is stored with ``condition: unresolved`` and nothing moves it. Worse, the
obvious fix — letting a later write flip the status field — destroys the only trustworthy part of
the record: what the user actually said, and when.

So settlement is **append-only and separate**:

* The evidence row is never edited. Its claim, quote and condition stay exactly as written.
* A later party appends a *settlement* row naming the claim (by ``claim_id``), the outcome, and the
  result that justifies it. Two settlements for one claim are two rows, history kept.
* The party that appends cannot be the agent that wrote the claim. Otherwise an agent grades its own
  condition, which is the one thing this plugin exists to prevent. ``by_kind`` is ``user`` or ``ci``;
  anything else is refused, and so is a ``by`` equal to the writing session's hash.
* A settlement may carry a code revision, and only one it can prove: 7–40 hex characters, or nothing.
  An empty revision is honest ("this was settled by a person reading the result"); a made-up or
  copied-from-the-wrong-repository sha is a fact that will be believed later.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

from . import store

SETTLEMENTS_FILE = "settlements.jsonl"

OUTCOME_SATISFIED = "satisfied"
OUTCOME_FAILED = "failed"
OUTCOME_UNKNOWN = "unknown"
OUTCOMES = (OUTCOME_SATISFIED, OUTCOME_FAILED, OUTCOME_UNKNOWN)

BY_USER = "user"
BY_CI = "ci"
SETTLER_KINDS = (BY_USER, BY_CI)

_REVISION = re.compile(r"^[0-9a-f]{7,40}$")


class SettlementRefused(Exception):
    """The settlement is not one we may store (self-grading, unprovable revision, unknown claim)."""


def claim_id(row: dict) -> str:
    """A stable id for one evidence row's claim.

    Derived from what identifies the claim rather than from its position in the file: the same claim
    written twice is one claim, and a row keeps its id when the file is pruned and rewritten.
    """
    basis = "\x1f".join(str(row.get(key) or "") for key in ("session", "target", "claim", "quote"))
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]


def read_settlements(path: Path) -> list[dict]:
    return [row for row in store.read_rows(path) if row.get("kind") == "settlement"]


def latest_by_claim(path: Path) -> dict[str, dict]:
    """Newest settlement per claim id (rows are appended in time order)."""
    latest: dict[str, dict] = {}
    for row in read_settlements(path):
        if row.get("claim_id"):
            latest[row["claim_id"]] = row
    return latest


def pending(evidence_path: Path, settlements_path: Path) -> list[dict]:
    """Evidence rows whose condition is unresolved and that no settlement has addressed yet.

    Newest first. Rows with ``condition: none`` are not here: nothing was left open, so there is
    nothing to settle.
    """
    settled = latest_by_claim(settlements_path)
    out = []
    for row in reversed(store.read_rows(evidence_path)):
        if row.get("condition") != "unresolved":
            continue
        cid = row.get("claim_id") or claim_id(row)
        if cid in settled:
            continue
        out.append({**row, "claim_id": cid})
    seen: set[str] = set()
    unique = []
    for row in out:  # the same claim written twice is one thing to settle
        if row["claim_id"] in seen:
            continue
        seen.add(row["claim_id"])
        unique.append(row)
    return unique


def status(settlements_path: Path, cid: str) -> str:
    """``satisfied`` / ``failed`` / ``unknown`` from the newest settlement, else ``unresolved``."""
    row = latest_by_claim(settlements_path).get(cid)
    if not row:
        return "unresolved"
    return row.get("outcome") or OUTCOME_UNKNOWN


def settle(evidence_path: Path, *, cid: str, outcome: str, by: str, by_kind: str, result: str,
           test: str | None = None, revision: str | None = None,
           settlements_path: Path | None = None, now: datetime | None = None) -> dict:
    """Append one settlement for ``cid``. Never touches the evidence row. Returns the stored row."""
    if outcome not in OUTCOMES:
        raise SettlementRefused(f"outcome must be one of {', '.join(OUTCOMES)}")
    if by_kind not in SETTLER_KINDS:
        raise SettlementRefused(
            "a settlement must come from the user or from CI; the agent that wrote the claim "
            "cannot settle it")
    by = (by or "").strip()
    if not by:
        raise SettlementRefused("a settlement must name who settled it")
    if not (result or "").strip():
        raise SettlementRefused("a settlement must carry the result that justifies it")
    revision = (revision or "").strip().lower()
    if revision and not _REVISION.match(revision):
        raise SettlementRefused(
            f"revision {revision!r} is not a git commit sha; leave it empty rather than pinning a "
            "value that will be read as a fact")

    rows = store.read_rows(evidence_path)
    match = next((row for row in rows if claim_id(row) == cid), None)
    if match is None:
        raise SettlementRefused(f"no evidence row has claim id {cid}")
    if (by or "") == (match.get("session") or ""):
        raise SettlementRefused(
            "this claim was written by the same session that is trying to settle it")

    row = {
        "kind": "settlement",
        "ts": store.format_ts(now or store.utc_now()),
        "claim_id": cid,
        "claim": match.get("claim"),
        "condition": match.get("condition"),
        "outcome": outcome,
        "by": by,
        "by_kind": by_kind,
        "result": result.strip(),
    }
    if (test or "").strip():
        row["test"] = (test or "").strip()
    if revision:
        row["revision"] = revision
    path = settlements_path or (evidence_path.parent / SETTLEMENTS_FILE)
    store.append_rows(path, [row])
    return row


__all__ = ["SETTLEMENTS_FILE", "OUTCOMES", "OUTCOME_SATISFIED", "OUTCOME_FAILED", "OUTCOME_UNKNOWN",
           "BY_USER", "BY_CI", "SETTLER_KINDS", "SettlementRefused", "claim_id", "read_settlements",
           "latest_by_claim", "pending", "status", "settle"]
