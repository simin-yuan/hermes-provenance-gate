"""The two Hermes hooks. Both fail open: an internal error is logged and the hook returns None."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from . import capture, store, verdict

logger = logging.getLogger(__name__)


class Gate:
    def __init__(self, directory: Callable[[], Path] = store.data_dir):
        self._directory = directory
        self.blocks = verdict.BlockCounter()

    def pre_llm_call(self, session_id: str = "", user_message: Any = None, platform: str = "",
                     **_: Any) -> None:
        """Record what the user said this turn. Never returns context."""
        try:
            if (platform or "") in capture.NON_USER_PLATFORMS:
                return None
            capture.capture(self._directory(), session_id or "", user_message)
        except Exception:
            logger.warning("provenance-gate: capture failed (ignored)", exc_info=True)
        return None

    def pre_tool_call(self, tool_name: str = "", args: Any = None, session_id: str = "",
                      turn_id: str = "", **_: Any) -> dict | None:
        try:
            return self._check(tool_name, args, session_id or "", turn_id or "")
        except Exception:
            logger.warning("provenance-gate: check failed for %s (allowed)", tool_name, exc_info=True)
            return None

    def _check(self, tool_name: str, args: Any, session_id: str, turn_id: str) -> dict | None:
        if tool_name not in verdict.GATED_TOOLS or not verdict.added_texts(tool_name, args):
            return None
        directory = self._directory()
        checks = verdict.evaluate(tool_name, args, session_id, capture.said_rows(directory), verdict.scope())
        if not checks:
            return None
        failed = [check for check in checks if not check.ok]
        now = store.utc_now()
        observe = verdict.mode() == verdict.MODE_OBSERVE
        if failed and not observe:
            rows = [verdict.evidence_row(c, tool_name, session_id, "blocked", now) for c in failed]
        else:
            rows = [verdict.evidence_row(c, tool_name, session_id, "pass" if c.ok else "would_block", now)
                    for c in checks]
        path = directory / store.EVIDENCE_FILE
        with store.locked(path):
            store.maybe_prune_unlocked(path, now, store.retention_days())
            store.append_rows_unlocked(path, rows)
        if not failed or observe:
            return None
        attempts = self.blocks.bump(session_id, turn_id, failed[0].target)
        return {"action": "block", "message": verdict.block_message(failed[0], attempts)}
