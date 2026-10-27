# Verdict convention v0.1.2 — release manifest (supersedes the v0.1.1 manifest)

Pinned on 2026-10-28 (Asia/Shanghai). Two earlier manifests are left in place: v0.1 recorded what v0.1
said, v0.1.1 recorded the correction, and this one records a rule that was wrong in both. A stamp that
is quietly overwritten records nothing.

## The convention document

    repo    simin-yuan/hermes-provenance-gate
    path    docs/verdict-convention.md
    commit  4948e1294f88e0afe9df1cbd5f189cef41c39273
    sha256  c4e85f6be3e701635bef07d6ed85e65066f64abb5d98fda8c6c016d31189d391   (14,230 B)

## What changed, and what was wrong

v0.1 and v0.1.1 both said, in §3, that a plugin exposing a mode switch should default to `observe`. As a
rule for every plugin that is wrong, and this document's own table is where it shows: `scamgate` guards
against fraud. A missed detection costs money that does not come back; a false positive costs one extra
confirmation. Its default has to act. Had it followed the convention, the convention would have made a
fraud guard weaker, which is the opposite of what a document about safety plugins should be able to do.

§3 now requires the switch and requires the author to declare the default and the trade-off behind it:
which error costs more in this plugin's domain. A default inherited from a convention has not made that
decision. The plugins audited here declare:

    degeneracy-guard   observe on a fresh install; an install that has run here before keeps act
    provenance-gate    observe
    scamgate           act
    skill-lineage      none, it never returns a directive

Nothing about the other two rules changed. `rule_key` grammar and the evidence row are as they were.

## The three releases pinned with it

    provenance-gate   0.1.6   f85afd255abcbfa03a66adfa6a221fb0cff283ec   tag v0.1.6
    degeneracy-guard  1.4.1   a8010a2726e037eaf27bafcfbf52a63fbb1d0361   tag v1.4.1
    skill-lineage     0.1.9   1e98f051599d1520d18dc35baf601a055c58d330   tag v0.1.9

Test counts on those commits, Windows, Python 3.11.16: 58 passed / 1 skipped, 50 passed / 1 skipped,
131 passed / 1 skipped.

Two of those releases fix faults found while writing v0.1.1:

- `degeneracy-guard` 1.4.0 decided its default from the incident log, so an install that had enforced for
  weeks without ever triggering a detection read as a first install and would have been switched to
  observe by the upgrade. 1.4.1 decides from the install's data directory, sampled before the package's
  own hooks can create it, and pins the answer so later evidence cannot re-label the install.
- `provenance-gate` 0.1.4–0.1.6 include `self_anchor`, which anchors text to Bitcoin. It could copy new
  bytes over a `body.txt` that an existing `.ots` proof still covered, leaving a timestamp that verified
  against nothing. It now refuses when the target holds different bytes, requires an explicit output
  directory, and treats identical bytes as a no-op.

## Catalog

    simin-yuan/hermes-agent   catalog/bump-2026-10-28   6ea28be767fcb29c0ef24640246327bdba7df63a

One commit on `NousResearch/hermes-agent@main` (e5ea5fd9ccdf2913c0b21b24eb3d22b321d2e2a6) bumping the
three entries. The branch is pushed; the pull request is not open.

The v0.1.1 manifest said creating that pull request failed with a permission error and inferred that the
account was not allowed to open one. That inference was wrong. GitHub's message, read in the browser on
2026-10-28, is `You've reached this repository's open pull request limit.` Five pull requests are open in
that repository (#144657 #142974 #142179 #141479 #141340); a sixth is refused. Nothing about the account
is restricted, and no workaround was used.

## They are now in a Bitcoin block

The v0.1 and v0.1.1 manifests, and the scamgate evidence archived earlier, were still carrying pending
attestations. `ots upgrade` had never been run on them — the proofs were complete, the attestations were
just waiting to be collected. After upgrading, all five verify end to end
(`node self_anchor/verify_self_anchor.js <dir>`):

    docs/anchor                block 973423
    docs/anchor-v2             block 973423
    scamgate anchor v1, v2, v3 block 973414 and 973423

## What is not claimed here

This is an integrity statement, not a priority claim. It does not establish that anything in these bytes
is novel: §5 of the convention names four open upstream items that already cover the approval-grain
problem, and §2 credits `scamgate` for the grain it already implements.
