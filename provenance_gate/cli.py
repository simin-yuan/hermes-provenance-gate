"""``hermes provenance-gate report | log | check | purge``."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Callable

from . import capture, store, verdict

HELP = "Summarise or inspect the provenance-gate evidence log, dry-run a check, or purge stored user text"
DESCRIPTION = (
    "provenance-gate keeps a local copy of the text of user messages and an evidence log under "
    "<HERMES_HOME>/plugin-data/provenance-gate/. These commands read or purge them."
)


def _non_negative(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a whole number") from None
    if number < 0:
        raise argparse.ArgumentTypeError(f"{number} is negative")
    return number


def setup(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="provenance_gate_command", metavar="{report,log,check,purge}")
    report = sub.add_parser("report", help="Count verdicts by reason and show the newest flagged claims")
    report.add_argument("--days", type=_non_negative, default=7, help="How many days back (default 7, 0 = all)")
    log = sub.add_parser("log", help="Show recent evidence rows (newest last)")
    log.add_argument("--limit", type=_non_negative, default=20, help="How many rows to show (default 20, 0 = all)")
    log.add_argument("--verdict", choices=("pass", "blocked", "would_block"), help="Only rows with this verdict")
    check = sub.add_parser("check", help="Dry-run the gate on some text against captured messages; writes nothing")
    check.add_argument("text", help="Text a memory or skill write would add")
    check.add_argument("--session", default="", help="Session id to search (with PROVENANCE_GATE_SEARCH=session, "
                       "the default, quotes are looked up only in this session)")
    purge = sub.add_parser("purge", help="Delete stored user text and evidence rows")
    group = purge.add_mutually_exclusive_group(required=True)
    group.add_argument("--older-than", type=_non_negative, metavar="DAYS", help="Delete rows older than DAYS days")
    group.add_argument("--all", action="store_true", help="Delete everything")


def make_handler(directory: Callable[[], Path] = store.data_dir) -> Callable[[argparse.Namespace], int]:
    def handle(args: argparse.Namespace) -> int:
        command = getattr(args, "provenance_gate_command", None)
        if command == "report":
            return cmd_report(directory(), args.days)
        if command == "log":
            return cmd_log(directory(), args.limit, args.verdict)
        if command == "check":
            return cmd_check(directory(), args.text, args.session)
        if command == "purge":
            return cmd_purge(directory(), args.older_than, args.all)
        print("usage: hermes provenance-gate {report,log,check,purge} ...")
        return 2
    return handle


REPORT_NEWEST = 10
REPORT_CLAIM_CHARS = 120


def cmd_report(directory: Path, days: int, now=None) -> int:
    """Counts of pass / would_block / blocked by reason, and the newest flagged claims. Read-only."""
    rows = store.read_rows_unlocked(directory / store.EVIDENCE_FILE)
    if days > 0:
        cutoff = (now or store.utc_now()) - timedelta(days=days)
        rows = [row for row in rows if (moment := store.parse_ts(row.get("ts"))) is not None and moment >= cutoff]
    window = f"last {days} day(s)" if days > 0 else "all rows"
    if not rows:
        print(f"no evidence rows ({window})")
        return 0
    counts = Counter((row.get("verdict") or "?", row.get("reason") or "-") for row in rows)
    print(f"evidence rows ({window}): {len(rows)}")
    for (verdict_name, reason), count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        print(f"  {verdict_name:<12} {reason:<22} {count}")
    flagged = [row for row in rows if row.get("verdict") in ("would_block", "blocked")][-REPORT_NEWEST:]
    if flagged:
        print(f"newest flagged claims (up to {REPORT_NEWEST}):")
        for row in reversed(flagged):
            claim = " ".join(str(row.get("claim") or "").split())
            if len(claim) > REPORT_CLAIM_CHARS:
                claim = claim[:REPORT_CLAIM_CHARS - 1] + "…"
            print(f"  {row.get('ts', '?')}  {row.get('verdict')}/{row.get('reason')}  {claim}")
    return 0


def cmd_log(directory: Path, limit: int, verdict_filter: str | None) -> int:
    rows = store.read_rows_unlocked(directory / store.EVIDENCE_FILE)
    if verdict_filter:
        rows = [row for row in rows if row.get("verdict") == verdict_filter]
    rows = rows[-limit:] if limit > 0 else rows
    if not rows:
        print("no evidence rows")
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))
    return 0


def cmd_check(directory: Path, text: str, session_id: str) -> int:
    said = capture.said_rows(directory, lock=False)
    search = verdict.search_mode()
    checks = verdict.evaluate("memory", {"action": "add", "target": "memory", "content": text},
                              session_id, said, verdict.scope(), search)
    if not checks:
        print("no decision claims: pass")
        return 0
    for check in checks:
        print(f"claim: {check.claim.sentence}")
        if check.ok:
            print(f'  pass: quote "{check.quote}" matched message {check.msg_id} ({check.matched_in})')
        else:
            detail = f' (quote "{check.quote}")' if check.quote else ""
            print(f"  fail: {check.reason}{detail}")
    failed = [check for check in checks if not check.ok]
    if not failed:
        print("verdict: pass")
        return 0
    print(f"verdict: {'would_block' if verdict.mode() == verdict.MODE_OBSERVE else 'block'}")
    print(verdict.block_message(failed[0], 1, search))
    return 1


def cmd_purge(directory: Path, older_than: int | None, purge_all: bool) -> int:
    now = store.utc_now()
    for name in (store.SAID_FILE, store.EVIDENCE_FILE):
        path = directory / name
        with store.locked(path):
            if purge_all:
                removed = len(store.read_rows_unlocked(path))
                store.rewrite_unlocked(path, [])
            else:
                removed = store.drop_older_than_unlocked(path, now - timedelta(days=older_than or 0))
        print(f"{name}: removed {removed} row(s)")
    return 0
