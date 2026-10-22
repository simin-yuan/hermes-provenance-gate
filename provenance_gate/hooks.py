"""The two Hermes hooks.

``pre_llm_call`` only records what the user typed; an error there is logged and ignored.
``pre_tool_call`` decides first and logs second: the verdict never depends on the evidence log
being writable, and an error after a block verdict cannot turn it into an allow. A memory or
skill_manage call whose arguments cannot be read, or that the checker fails on, is refused
(fail closed); every other tool is ignored.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from . import attribution, capture, repos, results, settlement, store, verdict

logger = logging.getLogger(__name__)


class Gate:
    def __init__(self, directory: Callable[[], Path] = store.data_dir):
        self._directory = directory
        self.blocks = verdict.BlockCounter()
        self.resolver = results.DiskResolver()

    def pre_llm_call(self, session_id: str = "", user_message: Any = None, platform: str = "",
                     sender_id: str = "", parent_session_id: str = "", **_: Any) -> None:
        """Record what the user typed this turn. Never returns context."""
        try:
            if (platform or "") in capture.NON_USER_PLATFORMS:
                return None
            capture.capture(self._directory(), str(session_id or ""), user_message,
                            sender_id=str(sender_id or ""), parent_session_id=str(parent_session_id or ""))
        except Exception:
            logger.warning("provenance-gate: capture failed (ignored)", exc_info=True)
        return None

    def _note_repo(self, session_id: str, args: Any) -> None:
        """Remember the repository this call is about. Costs a string scan, never raises."""
        try:
            if session_id:
                repos.record(self._directory(), session_id, args)
        except Exception:  # noqa: BLE001 - a bookkeeping miss must not touch the verdict
            logger.debug("provenance-gate: repository note skipped", exc_info=True)

    def _said(self, directory: Path | None) -> list[dict]:
        """Captured rows; an unreadable store counts as "nothing typed" (claims then fail)."""
        if directory is None:
            return []
        try:
            return capture.said_rows(directory)
        except Exception:
            logger.warning("provenance-gate: said.jsonl unreadable; treating as empty", exc_info=True)
        try:
            return capture.said_rows(directory, lock=False)
        except Exception:
            return []

    def pre_tool_call(self, tool_name: str = "", args: Any = None, session_id: str = "",
                      turn_id: str = "", **_: Any) -> dict | None:
        session_id, turn_id = str(session_id or ""), str(turn_id or "")
        self._note_repo(session_id, args)
        if tool_name not in verdict.GATED_TOOLS:
            return None
        try:
            directory = self._directory()
        except Exception:
            logger.warning("provenance-gate: plugin data directory unavailable", exc_info=True)
            directory = None
        search = verdict.search_mode()
        try:
            checks = verdict.evaluate(tool_name, args, session_id, self._said(directory), verdict.scope(), search,
                                      self.resolver)
        except verdict.ArgError as exc:
            checks = [verdict.failure(tool_name, verdict.REASON_UNREADABLE, str(exc))]
        except Exception:
            logger.error("provenance-gate: check failed for %s; refusing the write", tool_name, exc_info=True)
            checks = [verdict.failure(tool_name, verdict.REASON_INTERNAL, "")]
        if not checks:
            return None
        failed = [check for check in checks if not check.ok]
        observe = verdict.mode() == verdict.MODE_OBSERVE
        result = None
        if failed and not observe:
            try:
                message = verdict.block_message(failed[0], self.blocks.bump(session_id, turn_id, failed[0].target), search)
            except Exception:
                logger.error("provenance-gate: could not build the block message", exc_info=True)
                message = "provenance-gate: this write could not be checked. Nothing was written."
            result = {"action": "block", "message": message}
        self._log(directory, checks, failed, observe, tool_name, session_id)
        return result

    def _log(self, directory: Path | None, checks, failed, observe: bool, tool_name: str, session_id: str) -> None:
        """Append evidence rows. A failure here is logged; it never changes the verdict."""
        try:
            now = store.utc_now()
            if failed and not observe:
                rows = [verdict.evidence_row(c, tool_name, session_id, "blocked", now) for c in failed]
            else:
                rows = [verdict.evidence_row(c, tool_name, session_id, "pass" if c.ok else "would_block", now)
                        for c in checks]
            if directory is None:
                raise OSError("no plugin data directory")
            pin = repos.pin(directory, session_id)
            for row in rows:
                row["claim_id"] = settlement.claim_id(row)
                if row.get("condition") == attribution.CONDITION_UNRESOLVED:
                    # Left open at write time: record where the code stood, if that is knowable.
                    # An empty revision is a real answer and must not be tidied away later.
                    row["revision"] = pin
            path = directory / store.EVIDENCE_FILE
            with store.locked(path):
                store.maybe_prune_unlocked(path, now, store.retention_days())
                store.append_rows_unlocked(path, rows)
        except Exception:
            logger.warning("provenance-gate: could not write the evidence log (verdict unchanged)", exc_info=True)
