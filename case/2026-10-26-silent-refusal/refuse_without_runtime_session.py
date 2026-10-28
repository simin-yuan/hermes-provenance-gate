#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""refuse_without_runtime_session.py - reproduce "a process with no runtime identity
cannot rewrite the identity file", without any credential and without touching a
real database.

Everything runs inside a temporary SQLite file. Three things are attempted, and
each one is refused by the same gate:

  1. A process with no runtime identity injected by the host tries to rewrite the
     line that says who the agent is - while holding a ticket whose scope matches
     the patch exactly.  -> expect MISSING_RUNTIME_SESSION_ID, and one new line in
     the rejection log.
  2. The same submit without a ticket.           -> expect FORBIDDEN_OP.
  3. Counters afterwards: no event, unchanged document, ticket unconsumed.

The ticket is a fixed test fixture (`fixture-not-a-credential`), never a real
credential; it is planted directly into the temporary database. Step 2 exists to
answer the obvious objection "then just add a ticket" - a ticket is necessary and
not sufficient.

The module under test (`continuity_core.py`) is not published here. Point
CONTINUITY_SCRIPTS at a copy of it:

    set CONTINUITY_SCRIPTS=C:\path\to\scripts        (cmd)
    export CONTINUITY_SCRIPTS=/path/to/scripts       (bash)
    python refuse_without_runtime_session.py

Exit code 0 = all three layers refused as expected.
"""
import hashlib
import json
import os
import pathlib
import sys
import tempfile

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SCRIPTS = os.environ.get("CONTINUITY_SCRIPTS")
if not SCRIPTS:
    sys.exit("CONTINUITY_SCRIPTS is not set - point it at the directory that holds "
             "continuity_core.py, then run again.")
sys.path.insert(0, SCRIPTS)

# Simulate "a process the host never gave a runtime identity": drop every marker.
RUNTIME_MARKERS = ("HERMES_SESSION_ID", "HERMES_CRON_SESSION",
                   "HERMES_PARENT_START_MARKER", "HERMES_KERNEL_SENTINEL")
for _k in RUNTIME_MARKERS:
    os.environ.pop(_k, None)

import continuity_core as cc  # noqa: E402

FIXTURE = "fixture-not-a-credential"
SOUL_BEFORE = "# SOUL\nThe agent is called Ash. The person it works with is called Robin.\n"
PATCH = {"op": "replace_once", "find": "The agent is called Ash",
         "replace": "The agent is called Nobody"}


def fresh_db():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="refuse-demo-"))
    cc.DB_PATH = tmp / "probe.db"
    conn = cc.get_conn()
    cc.init_schema(conn)
    conn.execute("INSERT INTO canonical_state(doc_key, value_json, revision, updated_at) "
                 "VALUES('SOUL', ?, 1, '2026-01-01T00:00:00+00:00')", (SOUL_BEFORE,))
    conn.commit()
    conn.close()
    cc.register_writer("desktop_surface", "desktop", "agent", "T1")
    return tmp


def mint_fixture_token():
    """Fixture ticket: written straight into the temporary DB, scope matching this
    submit exactly. A real ticket can only be minted by a person typing a secret."""
    sha = cc.patch_sha(json.dumps(PATCH, ensure_ascii=False))
    conn = cc.get_conn()
    conn.execute("INSERT INTO approval_tokens(token_sha256, created_at, created_by, label, "
                 "scope_doc, scope_patch_sha) VALUES(?,?,?,?,?,?)",
                 (hashlib.sha256(FIXTURE.encode()).hexdigest(), "2026-01-01T00:00:00+00:00",
                  "fixture", "demo", "SOUL", sha))
    conn.commit()
    conn.close()
    return sha


def counters():
    """Read the state back through the module's own read-only helpers.

    Nothing below touches an internal table: whatever the public readers cannot
    see, this reproduction does not claim to know.
    """
    conn = cc.get_conn()
    head = cc.chain_head(conn)
    anomalies = cc.token_ledger_check(conn)
    conn.close()
    doc = cc.get_canonical("SOUL") or {}
    return head, doc, anomalies


def main():
    print("runtime markers:", {k: os.environ.get(k) for k in RUNTIME_MARKERS},
          "-> runtime_session_id() =", repr(cc.runtime_session_id()))
    ok = True

    print("\n[1] scoped ticket in hand, but the process has no runtime identity")
    fresh_db()
    sha = mint_fixture_token()
    print("    ticket scope patch_sha =", sha[:16], "(matches this submit)")
    prod_log = "--production-log" in sys.argv
    if not prod_log:
        # default: write the rejection log into the temporary directory, so a demo
        # never pollutes the real one
        cc.SUBMIT_REJECT_LOG = pathlib.Path(cc.DB_PATH).parent / "submit_reject.log"
    log_before = (cc.SUBMIT_REJECT_LOG.read_text(encoding="utf-8").count("\n")
                  if cc.SUBMIT_REJECT_LOG.exists() else 0)
    r1 = cc.submit_event("desktop_surface", "SOUL", PATCH, base_revision=1, event_type="PATCH",
                         evidence="repro: scoped ticket, no runtime session id",
                         approval_token=FIXTURE)
    print("    ->", r1.get("ok"), r1.get("error"))
    print("    detail:", (r1.get("detail") or "").replace("\n", " ")[:150])
    ok &= (r1.get("ok") is False and r1.get("error") == "MISSING_RUNTIME_SESSION_ID")

    print("\n[1b] did that refusal leave a log line?")
    text = (cc.SUBMIT_REJECT_LOG.read_text(encoding="utf-8")
            if cc.SUBMIT_REJECT_LOG.exists() else "")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    last = json.loads(lines[-1]) if lines else {}
    print("    log file:", cc.SUBMIT_REJECT_LOG)
    print("    lines   : %d -> %d" % (log_before, len(lines)))
    print("    last    :", json.dumps(last, ensure_ascii=False)[:220])
    logged = (len(lines) == log_before + 1 and last.get("error") == "MISSING_RUNTIME_SESSION_ID"
              and last.get("doc_key") == "SOUL" and last.get("verdict") == "REJECTED")
    print("    -> logged:", "yes" if logged else "no")
    ok &= logged

    print("\n[2] same submit, no ticket")
    fresh_db()
    r2 = cc.submit_event("desktop_surface", "SOUL", PATCH, base_revision=1, event_type="PATCH",
                         evidence="repro: no ticket")
    print("    ->", r2.get("ok"), r2.get("error"))
    ok &= (r2.get("ok") is False and str(r2.get("error", "")).startswith("FORBIDDEN_OP"))

    print("\n[3] state afterwards (third temporary DB, read back through the module's own helpers)")
    fresh_db()
    head, doc, anomalies = counters()
    print("    chain head        = %s  (no event was accepted)" % head)
    print("    SOUL revision     = %s" % doc.get("revision"))
    print("    body unchanged    = %s" % (doc.get("value") == SOUL_BEFORE))
    clean = (anomalies.get("ok") is True and anomalies.get("anomaly_count") == 0
             and anomalies.get("consumed_tokens") == 0)
    print("    credential ledger = %s"
          % ("clean - 0 anomalies, 0 credentials consumed" if clean else anomalies))
    ok &= (head is None and doc.get("revision") == 1 and doc.get("value") == SOUL_BEFORE
           and clean)

    print("\n" + "=" * 60)
    print("  result: %s" % ("PASS - all three layers refused, nothing changed" if ok
                            else "FAIL - at least one step did not refuse as expected"))
    print("=" * 60)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
