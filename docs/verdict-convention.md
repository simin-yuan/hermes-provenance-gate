# Hermes plugin verdict convention — v0.1.2

For plugins that answer `pre_tool_call`. Three rules: how to name a `rule_key`, how to expose an
observe/act switch, and what to record. Nothing here needs a change in Hermes.

**Status:** v0.1 published 2026-10-27; v0.1.1 the same day corrects one row of the plugin table; v0.1.2
replaces a single default with a declaration (see Changelog). Written against `NousResearch/hermes-agent@main`,
`hermes_cli/plugins.py` sha256 `0da0ebe88da76f4a73d77fcc6942ef549154abbc573d52d74c1608bcb61efce4`
(last touched 2026-10-25, commit `2eec2a7d24`).

---

## 0. What this document does not claim

It does not define the verdict verbs. Hermes already defines them, in source and in the user guide,
and any plugin author can read both. It does not propose a host API change. It does not introduce
`rule_key`; that field exists, is documented, and is already used by plugins outside this repository.

What it adds is narrower: a naming grammar for one field the host leaves to the plugin, a required
switch together with a required statement of which way it points and why, and a minimum record shape. If a later
reader finds that a section below merely restates the host, that section is a bug in this document.

## 1. The host contract, quoted

From `hermes_cli/plugins.py`, `_get_pre_tool_call_directive_details()` (lines 1907–1959):

> Check `pre_tool_call` hooks for `{"action": "block", "message"}` (veto; message becomes the tool
> result) or `{"action": "approve", "message", "rule_key"?}` (escalate ANY tool to the
> human-approval gate; `rule_key` picks the `[a]lways` allowlist grain). Precedence is
> `block` > `approve` > none, not registration order: any plugin's valid veto wins over an earlier
> plugin's request for human confirmation (#87420); among approves the first valid one wins.
> Irrelevant returns are ignored.

and from `website/docs/user-guide/features/hooks.md` (lines 568–582, 1955–1971):

> Precedence is `block` > `approve` > no directive, regardless of registration order […] `block`
> requires a non-empty `message` […] `approve` escalates the call to the existing human-approval
> gate; `message` and `rule_key` are optional, and denial, timeout, or gate error fails closed.

The user guide also carries the shape example `"rule_key": "optional:scope"` — a namespace, a colon,
and a key. That example is the whole specification `rule_key` currently has. The rest is left to the
plugin, which is why two plugins that both escalate produce allowlist entries that look nothing alike.

## 2. Rule 1 — `rule_key` is `<plugin>:<rule>`

A plugin that can return `approve` MUST return a `rule_key`, and the key MUST be:

| | |
|---|---|
| Shape | `<plugin>:<rule>` — exactly one colon |
| `<plugin>` | the `name:` in `plugin.yaml`, lowercase, hyphenated |
| `<rule>` | `[a-z0-9_]+` — the identifier of a **class of finding**, taken from a finite, documented set |
| Stable | the same finding class always yields the same key, across versions and across sessions |
| Never random | no per-call nonce, no timestamp, no hash of the arguments |

**Why the "never random" clause is the rule that matters.** Hermes does not currently let a plugin
choose which approval lifetimes to offer — `once`, `session` and `always` are all rendered, whatever
the plugin wanted (issue [#135350](https://github.com/NousResearch/hermes-agent/issues/135350),
opened 2026-10-08, with a proposed fix in [#86583](https://github.com/NousResearch/hermes-agent/pull/86583)).
Authors work around the missing grain by minting a fresh `rule_key` per call so that a session/always
tap cannot persist. The workaround works, and it costs: the buttons still render as if they will stop
the prompts, and every tap writes an allowlist entry that will never match again — the issue itself
calls it "dead entries". Once grain control lands, the workaround stops being needed; a grammar makes
the entries legible in the meantime.

A hash of the triggering content is **not** the same thing as a nonce, and this document does not
forbid it: it is reproducible for the same input, which is what makes an allowlist entry checkable at
all. What the clause forbids is a key that changes while nothing about the finding changes.

**Why per class, not per instance.** `[a]lways` means "stop telling me about this". A user who
answers it to a memory write with no source is not agreeing to every future memory write, and a key
derived from the text of one write cannot express the difference. Per class is the narrowest grain
that survives being written to disk.

**Why it must be finite and documented.** An allowlist is only reviewable if a reader can enumerate
what they have allowed. A plugin that emits keys from an unbounded space hands the user a list they
cannot audit.

### Current state across four plugins

| Plugin | Emits `rule_key` | Value |
|---|---|---|
| `degeneracy-guard` 1.4.0 | yes | `degeneracy-guard:<tool>` — per tool |
| `provenance-gate` 0.1.4 | yes (new) | `provenance-gate:<reason>` — per failure class |
| `scamgate` 0.3.3 | yes | `scamgate:<class>[:<sub>]:<hash10>` — seven shapes, suffix is a sha256 of the content that triggered the finding |
| `skill-lineage` 0.1.9 | n/a | never returns a directive; nothing to key |

### Three grains are in use, and the choice is visible

| Grain | Who | What one `[a]lways` covers |
|---|---|---|
| per tool | `degeneracy-guard` | every future loop-shaped call to that one tool |
| per failure class | `provenance-gate` | every future finding of that class, whatever the text |
| per triggering content | `scamgate` | a repeat of the same content; a different link, payee or payload asks again |

`scamgate` is deliberate about the third, in a comment in its own source: `one "always" never covers
other links`. That is the right grain for a destination-based finding — an allowlist entry for one
host should not silently cover the next host. It is a poor grain for a class-based finding, where it
produces an entry reachable only by an identical repeat.

The convention does not pick a winner. It requires the **prefix** to be stable and documented, and
requires the author to say which grain they chose, because the user answering `[a]lways` is being asked
a different question in each case. A reader who cannot tell which question they were asked cannot
answer it well.

## 3. Rule 2 — a plugin that can return a directive MUST expose both modes, and MUST declare its default and the reason

| Mode | Computes the decision | Records evidence | Returns a directive |
|---|---|---|---|
| `observe` | yes | yes, labelled as what it *would* have done | **no** |
| `act` | yes | yes | yes |

- The switch MUST be reachable without reading the source: an environment variable, a config key, or
  a documented CLI verb — and it MUST appear in the plugin's `description:` in the catalog, so a user
  can find it from the entry they installed from.
- `observe` MUST run the same detection path as `act`. A mode that skips the work is not an observe
  mode; it is an off switch wearing the name, and it cannot be used to calibrate anything.
- **There is no convention-wide default.** Each plugin MUST state, in its catalog description and its
  README, which mode a fresh install starts in and what that choice trades away. A plugin that
  silently inherits a default from a convention has not made the decision this rule is about.

The reason for the last clause: a default is a claim about which error costs more in that plugin's
domain, and the answer is not the same in two domains. Where a false positive interrupts work that was
going fine, starting in `observe` is right — the user should see the detector's judgment before it has
consequences. Where a missed detection loses money or cannot be undone, starting in `act` is right —
a user who did not read the README should not be less protected than one who did. A convention that
picked one of those for everyone would be optimising one plugin's cost at another plugin's expense,
and the loser would be whichever population was not consulted.

The plugins audited here, with the defaults they declare:

| plugin | default on a fresh install | and what that trades away |
|---|---|---|
| `degeneracy-guard` | `observe`; an install that has run here before keeps `act` | a false positive interrupts a working session, so a new install watches first — but an upgrade must not remove a protection already in use, which is why the mode is pinned rather than recomputed |
| `provenance-gate` | `observe` | a flagged write is a claim about the user's intent; a wrong accusation teaches the user to dismiss the plugin, and a plugin that is dismissed protects nothing |
| `scamgate` | `act` | a missed scam costs money that does not come back; a false positive costs one extra confirmation, so the safe default is the one that asks |
| `skill-lineage` | none — it never returns a directive | it records and verifies; there is nothing to observe or to act on |

Rationale for the mode existing at all: a safety plugin that can only be tested by being bitten will be
removed after its first false positive. Observe mode is how a user learns what the plugin considers a
violation *before* handing it the veto, and how an author measures the false-positive rate without
asking anyone to trust them. Of the four plugins audited here, three expose both modes and one has no
mode because it never acts.

## 4. Rule 3 — the record

An evidence row SHOULD contain, whatever else it carries:

```json
{"ts": "<ISO-8601 UTC>", "plugin": "<plugin name>", "rule": "<rule part of rule_key>",
 "verdict": "pass|would_block|asked|blocked", "tool": "<tool name>", "session": "<hash, not the id>"}
```

- `session` is a hash, not the raw session id. A log that outlives the session should not be a
  lookup table for who did what when.
- `verdict` uses the four words above and no others. `would_block` in observe mode and `blocked` in
  act mode are different facts and must not share a word.
- `risk` (an integer 0–100) is **optional**. It is optional because a score is only meaningful if it
  was calibrated against something, and a convention that demands a number from plugins that have no
  calibration makes them invent one. A plugin that emits `risk` MUST say in its README what the score
  was calibrated against. A plugin that does not emit it SHOULD emit `rule`.

## 5. Already open upstream

Listed so that nobody reads this document as a discovery. All four are open as of 2026-10-27:

| | |
|---|---|
| [#135350](https://github.com/NousResearch/hermes-agent/issues/135350) | plugin `approve` cannot control approval grain; documents the random-`rule_key` workaround and its cost |
| [#86583](https://github.com/NousResearch/hermes-agent/pull/86583) | `allowed_scopes`: let plugins narrow approval lifetime — the fix, open since 2026-08-15 |
| [#59298](https://github.com/NousResearch/hermes-agent/pull/59298) | surface approval rule keys in gateway clients — an allowlist nobody can see is not reviewable |
| [#106628](https://github.com/NousResearch/hermes-agent/pull/106628), [#96923](https://github.com/NousResearch/hermes-agent/pull/96923) | what the approval card shows for a plugin rule |

When `allowed_scopes` lands, §2's "never random" clause stays correct and the workaround it describes
becomes unnecessary. This document does not need to change; §5 will.

## 6. Why a standalone convention rather than a core change

Hermes does not take third-party integrations into core — `plugins/AGENTS.md`: "Plugins never touch
core"; `CONTRIBUTING.md`: "Third-Party Product Integrations: Ship as a Standalone Plugin". Written
down with those citations in `odaiameera/scamgate` → `docs/design/hermes-plugin.md` (lines 7–8).
A shared convention has to work for plugins that ship separately, which is what all four of these are.

## 7. Adoption checklist

For a plugin that wants to be listed as following v0.1:

- [ ] every `approve` return carries `rule_key = "<plugin>:<rule>"`, from a documented finite set
- [ ] `observe` and `act` both exist; `observe` runs the same detection and returns no directive
- [ ] the switch is findable from the catalog description, which also states the default mode and what
      that default trades away
- [ ] the default was chosen for this plugin's own error costs, not inherited from this document
- [ ] evidence rows carry `ts`, `plugin`, `rule`, `verdict`, `tool`, `session`(hash)
- [ ] `risk` either absent, or present with a stated calibration
- [ ] the plugin's README links to this document

## Changelog

- **v0.1** (2026-10-27) — first version. Three rules, four audited plugins, four open upstream items.
- **v0.1.1** (2026-10-27) — **correction.** v0.1 stated that `scamgate` emits no `rule_key`. It does:
  every one of its seven `approve` paths carries `scamgate:<class>[:<sub>]:<hash10>`, and the
  destination-scoped grain is deliberate (commented in its source). The claim came from reading the
  rendered hook output instead of the `Result` construction sites, which is exactly the kind of
  reading error a plugin author is in a position to make and a reader is not. The plugin table is
  corrected, and §2 now names the three grains in use and requires an author to say which one they
  chose. Nothing else changed; the other two rules never depended on that row.
- **v0.1.2** (2026-10-28) — **§3 no longer prescribes a default.** v0.1.1 said a plugin should default
  to `observe`. That reads as one rule for every plugin, and it is wrong for at least one of the four:
  `scamgate` guards against fraud, where a missed detection costs money and a false positive costs one
  confirmation, so its default has to act. Following the old wording would have meant weakening a
  fraud guard to satisfy a convention written by someone else — precisely the failure this document
  claims to be against. §3 now requires the switch, requires the author to declare the default and the
  trade-off, and lists what the four plugins declare. No plugin release accompanies this: the
  catalog entries already describe their modes, and the wording is being fixed before it is handed to
  anyone.
