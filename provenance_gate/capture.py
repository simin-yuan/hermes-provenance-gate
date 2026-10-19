"""Keep a local copy of what the user said, one row per turn, in ``said.jsonl``."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from . import store

DEDUP_SECONDS = 2.0

# Agents whose ``user_message`` is not typed by the user. Hermes builds these with a fixed
# ``platform``: the curator fork (agent/curator.py ``platform="curator"``), delegate_task children
# (tools/delegate_tool.py ``platform="subagent"``, the goal is written by the parent model) and
# cron runs (cron/scheduler.py ``platform="cron"``). Their prompts must never become evidence.
NON_USER_PLATFORMS = frozenset({"curator", "subagent", "cron"})


def message_text(user_message: Any) -> str:
    """Text of a user message: a string as is; for a list of content parts, the text parts
    joined with ``\\n`` (images, audio and other non-text parts are ignored)."""
    if isinstance(user_message, str):
        return user_message
    if not isinstance(user_message, list):
        return ""
    parts = []
    for part in user_message:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and part.get("type", "text") in ("text", "input_text"):
            text = part.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def message_id(session_id: str, ts: str, text: str) -> str:
    return hashlib.sha256((session_id + ts + text).encode("utf-8")).hexdigest()[:16]


def _is_duplicate(rows: list[dict], session_id: str, text: str, now: datetime) -> bool:
    for row in reversed(rows):
        moment = store.parse_ts(row.get("ts"))
        if moment is None:
            continue
        if abs((now - moment).total_seconds()) > DEDUP_SECONDS:
            if moment < now:
                return False  # rows are appended in time order; older ones cannot match
            continue
        if row.get("session_id") == session_id and row.get("text") == text:
            return True
    return False


def capture(directory: Path, session_id: str, user_message: Any, now: datetime | None = None,
            retention_days: int | None = None) -> dict | None:
    """Append the turn's user text to ``said.jsonl``; returns the row, or None when skipped
    (empty text, or the same text for the same session within two seconds)."""
    text = message_text(user_message)
    if not text.strip():
        return None
    now = now or store.utc_now()
    session_id = session_id or ""
    path = directory / store.SAID_FILE
    with store.locked(path):
        store.maybe_prune_unlocked(path, now, retention_days or store.retention_days())
        if _is_duplicate(store.read_rows_unlocked(path), session_id, text, now):
            return None
        ts = store.format_ts(now)
        row = {"ts": ts, "session_id": session_id, "msg_id": message_id(session_id, ts, text), "text": text}
        store.append_rows_unlocked(path, [row])
    return row


def said_rows(directory: Path, lock: bool = True) -> list[dict]:
    """Captured rows, oldest first. ``lock=False`` reads without creating the lock file (dry runs)."""
    path = directory / store.SAID_FILE
    rows = store.read_rows(path) if lock else store.read_rows_unlocked(path)
    return [row for row in rows if isinstance(row.get("text"), str)]
