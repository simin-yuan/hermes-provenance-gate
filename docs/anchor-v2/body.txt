# Verdict convention v0.1.1 — release manifest (supersedes the v0.1 manifest)

Pinned on 2026-10-27 (Asia/Shanghai). This manifest replaces the one stamped earlier today, because
v0.1 of the convention contained a false statement and was corrected to v0.1.1 the same day. The
earlier stamp is left in place: it records what v0.1 actually said, and a stamp that is quietly
overwritten records nothing.

## The convention document

    repo    simin-yuan/hermes-provenance-gate
    path    docs/verdict-convention.md
    commit  4e1bbafc6f64a240a0775e0f0ad7944d2a0c11e0   (tag v0.1.5, main)
    sha256  9d6b6429f7ce62e6017d8b8eb1fff7d97d198445416798ff90e240776dc88d1e   (11,496 B)

## What changed from v0.1, and why it matters

v0.1's plugin table said `scamgate` emits no `rule_key`. It does — seven `approve` paths, each keyed
`scamgate:<class>[:<sub>]:<hash10>`, with the destination-scoped grain chosen deliberately. The claim
came from reading the rendered hook output instead of the `Result` construction sites. The correction
is recorded in the document's Changelog, and §2 now names the three grains in use.

## The three releases that follow it

    provenance-gate   0.1.5   4e1bbafc6f64a240a0775e0f0ad7944d2a0c11e0   tag v0.1.5   simin-yuan/hermes-provenance-gate
    degeneracy-guard  1.4.0   516e69860e75272129e6eb657b38367ece0838f6   tag v1.4.0   simin-yuan/hermes-degeneracy-guard
    skill-lineage     0.1.9   1e98f051599d1520d18dc35baf601a055c58d330   tag v0.1.9   simin-yuan/hermes-skill-lineage

Test counts on those commits: 56 passed / 1 skipped (POSIX-only), 48 passed / 1 skipped,
131 passed / 1 skipped.

## The three catalog branches, pushed but not opened as pull requests

    simin-yuan/hermes-agent   catalog/provenance-gate-014   6418e3c14e   (0.1.4; superseded by 0.1.5)
    simin-yuan/hermes-agent   catalog/degeneracy-guard-140   5d1a310533
    simin-yuan/hermes-agent   catalog/skill-lineage-019     8b6254fab5

Every branch is `NousResearch/hermes-agent@main` (e5ea5fd9) plus one commit editing a single file under
`plugin-catalog/`. Creating the pull requests was attempted through the GitHub API and refused: REST
`POST /repos/NousResearch/hermes-agent/pulls` returns 404, and the same call through `gh pr create`
returns `simin-yuan does not have the correct permissions to execute CreatePullRequest`. The same
account opened nine pull requests in that repository between 2026-10-19 and 2026-10-25, including three
catalog entries, so this is not a standing property of the account. Nothing was created and nothing was
worked around.

## What is not claimed here

This manifest is an integrity statement, not a priority claim. It does not establish that anything in
these bytes is novel — §5 of the convention names four open upstream items that already cover the
approval-grain problem, and §2 credits `scamgate` for the grain it already implements.
