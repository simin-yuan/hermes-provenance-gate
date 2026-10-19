# provenance-gate

An agent should not be able to write "the user decided" unless the user said it.

`provenance-gate` is a plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

- It keeps a local copy of what the user types each turn (`pre_llm_call`).
- Before a `memory` or `skill_manage` write, it looks for sentences that say the user made a decision, such as "User decided …", "The owner approved …" or "用户决定 …" (`pre_tool_call`).
- Each such sentence must quote the user's own words, and the quote must appear verbatim in something the user said.
- If a sentence has no such quote, the write is refused and the model is told how to fix it. Nothing is written, and no human approval is involved.

Upstream references below are to Hermes commit
[`b4170e73b0bbd74b47dc567c36166d749c069699`](https://github.com/NousResearch/hermes-agent/tree/b4170e73b0bbd74b47dc567c36166d749c069699).

## Worked example

This is end-to-end test 2 (`tests/test_e2e_hermes.py`). It runs a real Hermes agent against a fake model server. The user says:

```
I decided we ship on Friday
```

The model tries to save the decision without the user's words:

```json
{"action": "add", "target": "memory", "content": "User decided to ship on Friday."}
```

The gate blocks the call. `MEMORY.md` is unchanged, and the model gets this tool result:

```json
{"error": "provenance-gate: this write states a user decision (\"User decided to ship on Friday.\") without the user's words. Quote the user's exact words in \"...\" if they said it, or write it as your own suggestion or as an open question. Nothing was written."}
```

The corrected write quotes the user (end-to-end test 1) and passes:

```json
{"action": "add", "target": "memory", "content": "User decided to ship on Friday (\"I decided we ship on Friday\")"}
```

The quote and the message it matched are recorded in `evidence.jsonl`:

```json
{"ts": "…", "session": "ef3dd55a7392", "tool": "memory", "target": "memory:memory", "claim": "User decided to ship on Friday (\"I decided we ship on Friday\")", "quote": "I decided we ship on Friday", "msg_id": "3512fe464234f7d5", "matched_in": "this-session", "verdict": "pass"}
```

The third block of the same write target in one turn adds `Stop retrying this write; ask the user instead.` It is still a block (end-to-end test 3b).

## Install

Either clone this repository into your plugins directory:

```bash
git clone https://github.com/simin-yuan/hermes-provenance-gate ~/.hermes/plugins/provenance-gate
hermes plugins enable provenance-gate
```

or let Hermes install it from Git:

```bash
hermes plugins install https://github.com/simin-yuan/hermes-provenance-gate --enable
```

Then check it:

```bash
hermes plugins doctor provenance-gate
```

The plugin needs Hermes 0.22.2 or newer (`requires_hermes: ">=0.22.2"`). It has been tested on the v0.22.2 release, on the pinned commit above and on Hermes `main` at `493208879a3248c6a4b844981d2c31c78ae00929`. It has no Python dependencies.

## Configuration

These are environment variables, for example in `~/.hermes/.env`:

| Variable | Values | Default |
|---|---|---|
| `PROVENANCE_GATE_MODE` | `block`: refuse the write. `observe`: never refuse, log `would_block` instead. | `block` |
| `PROVENANCE_GATE_SCOPE` | `decisions`, or `decisions+preferences` to also require quotes for "prefers / likes / 喜欢 / 偏好 …" | `decisions` |
| `PROVENANCE_GATE_RETENTION_DAYS` | Days of stored user text and evidence rows to keep | `30` |

Commands:

```bash
hermes provenance-gate log [--limit N] [--verdict pass|blocked|would_block]
hermes provenance-gate check "<text>" [--session ID]   # dry run, writes no rows
hermes provenance-gate purge (--older-than DAYS | --all)
```

## How a write is checked

1. **What is checked.** Only text the call adds is checked:
   - `memory`: `content` (or its alias `new_text`) of `add` and `patch`, as a single operation or as each item of `operations` (`tools/memory_tool.py:250-289`, `:373-446`).
   - `skill_manage`: `content` of `create`, `new_string` or full `content` of `patch`, `file_content` of `write_file`, and `content` of the legacy `edit`. Both the `operations` shape and the legacy flat shape are handled (`tools/skill_manager_tool.py:722-728`, `:862-919`).
   - Removals and deletions add nothing, so they are not checked.
2. **Sentences.** The added text is split into lines and sentences, in English and Chinese. Markdown list markers are dropped and table rows are split into cells. A sentence end inside a quotation does not split the sentence.
3. **Claims.**
   - A sentence is a decision claim when its subject is `user`, `the user`, `owner` or `the owner`, followed by a decision verb: decided, chose, agreed, approved, confirmed, signed off, instructed, asked me to, told me to, wants us to, said to, gave the go-ahead, rejected, vetoed. In Chinese, the subject is 用户 / 主人 / 老板 followed by 决定, 同意, 批准, 确认, 拍板, 要求, 指示, 让我, 选择了, 否决 or 拒绝了.
   - These are not claims:
     - pronouns alone, and non-user subjects ("CI decided", "测试已确认")
     - negated forms ("has not decided", "hasn't approved", 未决定, 尚未同意)
     - open and conditional forms ("may decide", "asked whether", "if the user approved", 待用户确认, 是否同意, 由用户决定, 用户确认后)
     - questions
     - text inside quotes or backticks
   - The patterns are data in [`provenance_gate/patterns.py`](provenance_gate/patterns.py), and every pattern and every negated form has a test.
4. **Evidence.** A claim passes when it contains a quote in "…", “…”, '…' (two or more words), 「…」 or 『…』 that occurs verbatim in a captured user message.
   - Matching normalizes NFKC, whitespace, letter case, straight vs curly quotes and the quote's trailing punctuation. Nothing else is normalized.
   - A quote may not cut a word in half, including across `-`, `_`, `.`, `/` or `'`. For example, "rename it to atlas" does not match "rename it to atlas-core".
   - The current session's messages are searched first, then every retained message in the profile. The evidence row records which one matched (`this-session` or `other-session`).
   - A quote shorter than 4 words (Latin) or 4 characters (CJK) counts only if it is the user's whole message, such as "yes" or "好".
   - Each claim in a write needs its own quote.

## Where the hooks fire

These were checked at the pinned commit and are exercised by `tests/test_e2e_hermes.py`.

- **Foreground turns.** `pre_llm_call` receives the user's message, and `pre_tool_call` follows with the same `session_id` (`website/docs/user-guide/features/hooks.md:451,456`; `agent/turn_context.py:806-821`; `agent/inline_tool_executors.py:20-28`). A `{"action": "block", "message": …}` return becomes the tool result the model sees (`hermes_cli/plugins.py:1979-2023`).
- **Background self-improvement review fork.** This fork is `_persist_disabled`, so it never fires `pre_llm_call`, and its review prompt is never captured (`agent/background_review.py:1002`, `agent/turn_context.py:806`). Its `memory` and `skill_manage` calls do fire `pre_tool_call`, carrying the parent session's id (`agent/background_review.py:1005`). They are checked against what the user said in that session.
- **Curator fork.** Its `skill_manage` calls are checked (`agent/curator.py:1105-1140`).
- **Text the user did not type is never captured.** The curator fork (`platform="curator"`, `agent/curator.py:1119`), `delegate_task` children (`platform="subagent"`, whose goal is written by the parent model, `tools/delegate_tool.py:282`) and cron runs (`platform="cron"`, `cron/scheduler.py:2524`) all fire `pre_llm_call`. Their prompts are not captured, so they cannot serve as evidence. Writes from these agents are still checked.
- **Unattended runs.** The gate never uses Hermes' approval prompt, so it behaves the same in the CLI, the gateway, cron and unattended runs.
- **Errors.** If the plugin itself fails, it logs the error and allows the call.

## What it does not do

- **It is a tripwire, not a complete filter.** A decision worded outside the patterns ("we're going with Friday, per the user") is not detected.
- **No fuzzy or semantic matching.** A paraphrase of what the user said is not evidence, and an exact quote with a changed word fails.
- **The quote lives in the written text.** Hermes tool arguments cannot carry evidence on the side. A `pre_tool_call` `modify` return can only add or override keys (`hermes_cli/plugins.py:2006-2013`), and the memory executor passes only its named arguments to the tool (`agent/inline_tool_executors.py:142-149`). So the user's words appear in `MEMORY.md` or the skill file next to the claim.
- **A match shows that the words were said, not what they answered.** A "yes" that is the user's whole message passes, whatever question it answered.
- **Only the `memory` and `skill_manage` tools are checked.** Writes to memory or skill files through `terminal`, `write_file` or `patch`, and external memory providers, are not checked.
- **A user who never typed the words cannot be quoted.** This is by design. Words that reached the agent some other way, such as a voice note the platform did not transcribe into the message, do not count.

## Privacy

- **What is stored.** The plugin keeps the text of each user message (not images or audio) in `<HERMES_HOME>/plugin-data/provenance-gate/said.jsonl`. Evidence rows, which include the quote and the claim, go in `evidence.jsonl` in the same folder. It is profile-aware (`plugins/plugin_storage.py:27`).
- **Retention.** Rows older than `PROVENANCE_GATE_RETENTION_DAYS` (default 30) are dropped from both files, at most once an hour, when a new row is written. `hermes provenance-gate purge --older-than DAYS` or `--all` deletes rows on demand.
- **Nothing leaves the machine.** The plugin makes no network requests.

## Related

- **memory-shield** controls who may change an agent's memory.
- **hermes-dreaming** corroborates remembered facts against what people said, in a nightly pass.
- **provenance-gate** checks claims about user decisions at the moment they are written.

## Tests

```bash
python -m pip install pytest
python -m pytest -v                 # unit tests and corpora; end-to-end tests skip without Hermes
```

The end-to-end tests run when Hermes is importable, for example from a Hermes virtualenv:

```bash
git clone https://github.com/NousResearch/hermes-agent && cd hermes-agent
git checkout b4170e73b0bbd74b47dc567c36166d749c069699
uv venv -p 3.14 && uv pip install -e . --group dev
cd /path/to/provenance-gate && PROVENANCE_GATE_REQUIRE_E2E=1 /path/to/hermes-agent/.venv/bin/python -m pytest -v
```

Corpora (`tests/test_corpora.py`, run with `-s` to print the counts):

- **`tests/corpus/legit.jsonl`:** 159 English and Chinese memory and skill lines that are not decision claims. Examples are environment facts, conventions, preferences, negated and open decisions, quoted code, YAML, JSON and Markdown tables, and "confirmed" or "decided" with a non-user subject. Each is sent as a `memory` add and as a `skill_manage` create, for 318 calls. Blocked: 0.
- **`tests/corpus/claims.jsonl`:** 121 decision-claim cases with their expected outcome. They cover:
  - a valid quote, a missing quote, and a quote the user never said
  - a quote said in another session
  - a short quote that is the whole message, and a short quote inside a longer message
  - several claims with one quote missing
  - memory batches and the `skill_manage` create, patch, write_file and legacy shapes

  Expected outcome met: 121 of 121.

CI (`.github/workflows/ci.yml`) has three parts:

- **Unit tests and corpora** on Python 3.11 to 3.14, on Ubuntu and Windows.
- **The `hermes-tools` job.** Hermes at the pinned commit on Python 3.14, all tests including end-to-end, `hermes plugins validate` (no warnings) and `hermes plugins doctor provenance-gate --ci`.
- **A weekly scheduled run** of the same job against Hermes `main`.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
