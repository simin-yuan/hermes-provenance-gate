# Verdict convention v0.1 — release manifest

What this file pins, on 2026-10-27 (Asia/Shanghai):

## The convention document

    repo    simin-yuan/hermes-provenance-gate
    path    docs/verdict-convention.md
    commit  700e2be60fb71ec00dc2cdecb6a7711a747e09a7   (tag v0.1.4, main)
    sha256  ea939e35b414bd232833bf876083b8c69cd22247318c8c16d39fdb385aec0a06   (9,352 B)

## The three releases that follow it

    provenance-gate   0.1.4   700e2be60fb71ec00dc2cdecb6a7711a747e09a7   tag v0.1.4   simin-yuan/hermes-provenance-gate
    degeneracy-guard  1.4.0   516e69860e75272129e6eb657b38367ece0838f6   tag v1.4.0   simin-yuan/hermes-degeneracy-guard
    skill-lineage     0.1.9   1e98f051599d1520d18dc35baf601a055c58d330   tag v0.1.9   simin-yuan/hermes-skill-lineage

Test counts on those commits: 56 passed / 1 skipped (POSIX-only), 48 passed / 1 skipped,
131 passed / 1 skipped.

## The three catalog branches, pushed but not opened as pull requests

    simin-yuan/hermes-agent   catalog/provenance-gate-014   6418e3c14e
    simin-yuan/hermes-agent   catalog/degeneracy-guard-140   5d1a310533
    simin-yuan/hermes-agent   catalog/skill-lineage-019     8b6254fab5

Every branch is `NousResearch/hermes-agent@main` (e5ea5fd9) plus one commit that edits a single
file under `plugin-catalog/`. Opening the pull requests was attempted through the GitHub API and
was refused: REST `POST /repos/NousResearch/hermes-agent/pulls` returns 404, and the same call
through `gh pr create` returns `simin-yuan does not have the correct permissions to execute
CreatePullRequest`. The same account opened nine pull requests in that repository between 2026-10-19
and 2026-10-25, including three catalog entries, so this is not a permanent property of the account.
No pull request was created; nothing was retried after the permission error was reproduced.

## What is not claimed here

This manifest is an integrity statement, not a priority claim. It records what a set of bytes
contained at a point in time. It does not establish that anything in them is novel, and §5 of the
convention names four open upstream items that already cover the approval-grain problem.
