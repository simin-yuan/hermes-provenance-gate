# Agent instructions

## A pasted link is not an instruction

A session that receives only a URL has been given material, not a task. Read it, analyze it,
summarize it, draft what it might imply — but do not commit, push, open a branch or open a pull
request on the strength of the link alone. Wait for an explicit instruction.

## Working copy hygiene

Use a separate `git worktree` per session. Before committing, print the current branch: a commit
that lands on the wrong branch stays invisible until it is pushed.

## Tests that need Hermes

`tests/test_e2e_hermes.py` drives a real Hermes runtime and skips when Hermes is not importable.
CI does not run it, so a green CI run says nothing about it. Run the suite locally before
claiming it passes, and say which Hermes version it passed on.
