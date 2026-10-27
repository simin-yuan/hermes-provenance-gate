# Hermes plugin verdict convention — v0.1

For plugins that answer `pre_tool_call`. Three rules: how to name a `rule_key`, how to expose an
observe/act switch, and what to record. Nothing here needs a change in Hermes.

**Status:** published 2026-10-27. Written against `NousResearch/hermes-agent@main`,
`hermes_cli/plugins.py` sha256 `0da0ebe88da76f4a73d77fcc6942ef549154abbc573d52d74c1608bcb61efce4`
(last touched 2026-10-25, commit `2eec2a7d24`).

---

## 0. What this document does not claim

It does not define the verdict verbs. Hermes already defines them, in source and in the user guide,
and any plugin author can read both. It does not propose a host API change. It does not introduce
`rule_key`; that field exists, is documented, and is already used by plugins outside this repository.

What it adds is narrower: a naming grammar for one field the host leaves to the plugin, a required
switch that two of the four plugins audited here do not have, and a minimum record shape. If a later
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
| `degeneracy-guard` 1.3.0 | yes | `degeneracy-guard:<tool_name>` — per tool |
| `provenance-gate` 0.1.4 | yes (new) | `provenance-gate:<reason>` — per failure class |
| `skill-lineage` 0.1.9 | n/a | never returns a directive; nothing to key |
| `scamgate` 0.3.3 | **no** | escalates with `message` only — an `ask` from it can only be answered with the blanket `always` |

## 3. Rule 2 — a plugin that can return a directive MUST expose `observe` and `act`

| Mode | Computes the decision | Records evidence | Returns a directive |
|---|---|---|---|
| `observe` | yes | yes, labelled as what it *would* have done | **no** |
| `act` | yes | yes | yes |

- The switch MUST be reachable without reading the source: an environment variable, a config key, or
  a documented CLI verb — and it MUST appear in the plugin's `description:` in the catalog, so a user
  can find it from the entry they installed from.
- `observe` MUST run the same detection path as `act`. A mode that skips the work is not an observe
  mode; it is an off switch wearing the name, and it cannot be used to calibrate anything.
- `act` is not required to be the default. But **a plugin that acts by default MUST say so** in its
  catalog description, in those words, because the first time a user meets the plugin is in the
  approval prompt.

Rationale: a safety plugin that can only be tested by being bitten will be removed after its first
false positive. Observe mode is how a user learns what the plugin considers a violation *before*
handing it the veto, and how an author measures the false-positive rate without asking anyone to
trust them. Of the four plugins audited here, two have no switch at all — one always acts, one has
two modes and no third.

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
- [ ] the switch is findable from the catalog description, and the description states the default mode
- [ ] evidence rows carry `ts`, `plugin`, `rule`, `verdict`, `tool`, `session`(hash)
- [ ] `risk` either absent, or present with a stated calibration
- [ ] the plugin's README links to this document

## Changelog

- **v0.1** (2026-10-27) — first version. Three rules, four audited plugins, four open upstream items.
