# A refusal you can measure (2026-10-26)

An agent that keeps a persistent identity file — the paragraph that states its own name,
the name it uses for the person it works with, and how it signs outgoing work — has a
specific failure mode: the file is text, and text can be edited.

This folder holds the artefacts from one attempt to edit such a file, the gate that
stopped it, and the gap that the check afterwards exposed.

| File | What it is |
|---|---|
| `log-fix.diff` | The seven inserted lines that made the refusal leave a trace. No allow/deny condition was changed. |
| `refuse_without_runtime_session.py` | Reproduces the refusal in a temporary SQLite file, with no credential and no network. Prints the counters afterwards. |

## What happened

A maintenance probe asked a helper script which arguments it accepted by running it with
`--help`. The script had no argument parser: `--help` was ignored and the normal write path
ran. The submit was refused with `MISSING_RUNTIME_SESSION_ID` before any event was appended.

## What the gate checks

Three conditions, each on a different axis:

1. the writer must be registered, with a surface and a trust tier;
2. a rewriting edit needs a single-use ticket, minted from a secret typed by a human in a
   terminal carrying no agent environment variables, scoped to the exact hash of the patch;
3. the submitting process must carry a runtime session identifier injected by the host.
   It is never accepted as a function argument — a model must not be able to hand itself a
   session identity.

The probe failed the third. It could not have satisfied the second either, but it never got
that far.

## The readings, which are the point

| Reading | Before the probe | After |
|---|---|---|
| events for the document | 15 | 15 |
| latest event | unchanged | unchanged |
| document revision | 6 | 6 |
| unused tickets | 0 | 0 |

"It was blocked" is worth very little on its own. A gate that announces a block while
writing anyway — or that fails open when a dependency is missing — tells the same story.
Re-reading the counters is what separates the two.

## The gap, and the fix

That refusal returned before any write and **wrote no log line either**. A week later the
only evidence would have been an unchanged event count, which cannot distinguish *the gate
said no* from *nothing was submitted*. The other rejection path in the same system already
appends a JSON line to a rejection log; the asymmetry was the finding.

`log-fix.diff` is the fix: one logging call before the `return`, decision untouched — seven
inserted lines. Verified by running the script here twice: the temporary log goes 0 → 1
line, and pointed at a real rejection log with `--production-log`, 3 → 4.

## Reproducing it

The module under test is not published in this repository. Point `CONTINUITY_SCRIPTS` at
your own copy:

```
set CONTINUITY_SCRIPTS=C:\path\to\scripts        # cmd
export CONTINUITY_SCRIPTS=/path/to/scripts         # bash
python refuse_without_runtime_session.py
```

```
[1]  scoped ticket, no runtime identity -> MISSING_RUNTIME_SESSION_ID
[1b] did that refusal leave a log line? -> yes: 0 lines before, 1 after
[2]  same submit, no ticket             -> FORBIDDEN_OP
[3]  no event accepted | revision 1 | body unchanged  | credential 0 consumed
result: PASS - all three layers refused, nothing changed
```

Step 2 closes the obvious objection — "then just add a ticket". A ticket is necessary and
not sufficient.

## What this does not prove

- The environment-variable check is an **advisory layer**, not a cryptographic one. The real
  barrier is the ticket, which only a human can mint.
- A log line proves a refusal **happened**, not that refusal **always happens**. The
  guarantee rests on the counters and the mechanism, not on the log.
- One incident is not a rate. It is a sample with a timestamp.

## The durable habit

After editing an identity file, start a **new** process and ask it the plain question: what
are you called, what am I called, how do you sign? A change only the current process
believes in is not a change.
