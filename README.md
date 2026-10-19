# provenance-gate

An agent should not be able to write "the user decided" unless the user said it.

`provenance-gate` is a plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

- It keeps a local copy of what the user types each turn (`pre_llm_call`).
- Before a `memory` or `skill_manage` write, it looks for sentences that say the user made a decision, such as "User decided …", "The owner approved …" or "用户决定 …" (`pre_tool_call`).
- Each such sentence needs a quote in "…" that appears verbatim in text the user typed in this session and shares key words with the decision.
- Otherwise the write is refused and the model is told how to fix it. Nothing is written, and no human approval is involved.

What is checked is narrower than "the user said it". See [What is checked](#what-is-checked) and the [threat model](#threat-model).

Upstream references below are to Hermes commit
[`b4170e73b0bbd74b47dc567c36166d749c069699`](https://github.com/NousResearch/hermes-agent/tree/b4170e73b0bbd74b47dc567c36166d749c069699).

## Worked example

This is end-to-end test 2 (`tests/test_e2e_hermes.py`). It runs a real Hermes agent against a fake model server. The user types:

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

The quote and the message it matched are recorded in `evidence.jsonl`. `sender_id` is empty here because the test agent has no platform user:

```json
{"ts": "…", "session": "fc8fab46bd69", "tool": "memory", "target": "memory:memory", "claim": "User decided to ship on Friday (\"I decided we ship on Friday\")", "quote": "I decided we ship on Friday", "msg_id": "88e4b5036ce39ce0", "matched_in": "this-session", "sender_id": "", "verdict": "pass"}
```

A quote of something unrelated the user typed is refused too (end-to-end test 12, after the user typed "can you look at the logs for me"):

```json
{"error": "provenance-gate: this write states a user decision (\"The user approved deleting the prod database (\"can you look at the logs for me\").\") without the user's words. The quoted words (\"can you look at the logs for me\") do not share key words with the decision. Quote the user's exact words in \"...\" if they said it, or write it as your own suggestion or as an open question. Nothing was written."}
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
| `PROVENANCE_GATE_SEARCH` | `session`: quotes must come from this session. `profile`: if not found in this session, every retained message in the profile is searched too. | `session` |
| `PROVENANCE_GATE_SCOPE` | `decisions`, or `decisions+preferences` to also require quotes for "prefers / likes / 喜欢 / 偏好 …" | `decisions` |
| `PROVENANCE_GATE_RETENTION_DAYS` | Days of stored user text and evidence rows to keep | `30` |

Commands:

```bash
hermes provenance-gate log [--limit N] [--verdict pass|blocked|would_block]
hermes provenance-gate check "<text>" [--session ID]   # dry run, writes no rows
hermes provenance-gate purge (--older-than DAYS | --all)
```

`--limit` and `--older-than` must be zero or more.

## What is checked

1. **The text a write adds.** Hermes decodes some argument shapes only when it executes the tool (`tools/arg_coercion.py`, applied at `agent/inline_tool_executors.py:305` and `model_tools.py:889`). The plugin decodes them the same way first:
   - `operations` sent as a JSON string is decoded.
   - A single object in `operations` counts as a list of one.
   - Operations sent as JSON strings are decoded.
   - `memory` with an empty `operations` list runs its flat fields, so those are checked.

   Then:
   - `memory`: `content` (or its alias `new_text`) of `add` and `patch` (`tools/memory_tool.py:250-289`, `:373-446`).
   - `skill_manage`: `content` of `create` and the legacy `edit`, `new_string` or `content` of `patch`, `file_content` of `write_file` (`tools/skill_manager_tool.py:722-728`, `:862-919`).
   - Removals add nothing.

   If the plugin cannot read the arguments of a `memory` or `skill_manage` call, the call is refused. Examples: an `operations` string that is not JSON, an item that is not an object, a text field that is not a string.
2. **Sentences.** The text is normalized first: NFKC, which also folds full-width letters, with zero-width characters and soft hyphens removed. It is then split into lines and sentences, in English and Chinese. A sentence end inside a quotation does not split the sentence.
3. **Generic-actor rule.** Not every sentence about "the user" is about the person the agent works for. These are not claims:
   - lines inside a fenced code block or indented by four spaces or a tab, so a `# user confirmed …` inside code is a comment, not a heading
   - Markdown table rows, such as `| User confirmed the order | checkout.js |`
   - key/value lines whose key holds the decision words and whose value is a scalar, such as `user confirmed: false` or `user approved = true`
   - key/value lines with an identifier key, such as `on_confirm: user confirmed the modal, emit event`
   - QA and spec lines starting with a label such as `Bug #12:`, `Step 3:`, `Expected:` or `Given …`
   - sentences where a study, scenario or example word comes before the subject: "In the usability study, the user chose …", "Each user confirmed …", 测试中, 例如
   - Chinese rules and conditions: 用户确认收货后，订单完成; 用户同意隐私政策才能注册
   - a third party or a population: repo owner, 仓库主人, 新用户, and the plural "users"

   All of these are data in [`provenance_gate/patterns.py`](provenance_gate/patterns.py), with tests.
4. **Claims.**
   - **English:** a sentence is a decision claim when its subject is `user`, `the user`, `owner` or `the owner`, followed by: decided, chose, agreed, approved, confirmed, signed off, instructed, asked me to, told me to, wants us to, said to, gave the go-ahead, rejected, vetoed, OK'd / okayed, said yes to, greenlit. The phrases "the user's decision: …", "per the user" and "according to the user's instructions" also count.
   - **Chinese:** the subject is 用户 / 用戶 / 主人 / 老板 / 老闆, optionally followed by 已经, 明确地 or 正式 and spaces, then 决定 / 決定, 同意, 批准 / 批準, 确认 / 確認, 拍板, 要求, 指示, 让我, 选择了, 否决 or 拒绝了 / 拒絕了.
   - **Not claims:**
     - negated forms ("has not decided", 未决定, 尚未同意)
     - modal forms ("may decide", "will decide later")
     - conditional forms ("if", "unless", "whether", "once", "until"; 待用户确认, 是否同意, 由用户决定, 用户确认后再…)
     - questions
   - **Not exemptions:** a non-conditional opener ("When asked,", "Today"), a trailing "(ticket?)" and a tag such as ", ok?". Words that only look like an exemption also don't count: 用户确认后天…, 用户决定前端…, 用户确认时间… are claims.
5. **Quotes do not hide claims.** Quote marks are ordinary text until a quoted span is verified.
   - Only quotes found in what the user typed are removed before the claim check runs again.
   - So a claim wrapped in "…", '…', “…”, 「…」, 『…』 or backticks is still seen, as is a claim between two inch marks (`27" wide. The user approved … 6" long`).
   - A quoted user message that happens to contain decision words is the user's text, not a claim.
6. **Evidence.** A claim passes when it contains a quote in "…", “…”, '…' (two or more words), 「…」 or 『…』 that meets all of these:
   - **Verbatim.** It appears verbatim in text the user typed. Matching normalizes NFKC, whitespace, letter case, straight vs curly quotes and the quote's trailing punctuation, and nothing else. A quote may not cut a word in half, including across `-`, `_`, `.`, `/` or `'`.
   - **This session.** It comes from this session (`PROVENANCE_GATE_SEARCH=session`, the default). The background review fork carries the parent's session id (`agent/background_review.py:1005`), so its writes are checked against what the user typed in that session.
   - **About the claim.** It shares at least two content tokens with the rest of the sentence. Latin tokens are words of three or more letters, lower-cased, minus a short stopword list. CJK tokens are character bigrams.
   - **Short quotes.** A quote shorter than 4 words (Latin) or 4 characters (CJK), such as "yes" or "好", counts only if it is the user's whole message, in this session, among their last 3 messages.

   Each claim in a write needs its own quote. A write with more than 100 claims is refused.
7. **Unattended runs.** The gate never uses Hermes' approval prompt, so it behaves the same in the CLI, the gateway, cron and unattended runs.
8. **Errors.**
   - The verdict is decided before anything is logged. If the evidence log cannot be written, a warning is logged and the verdict stands.
   - An unreadable `said.jsonl` counts as "nothing typed", so claims are refused.
   - An internal error while checking a `memory` or `skill_manage` call refuses the call.
   - A failure in `pre_llm_call` (capture) is logged and ignored.
9. **Speed.** The check is linear in the size of the write. A test checks 1 MiB of adversarial text in under 2 seconds (`tests/test_performance.py`).

## What is captured

- **What is stored.** `pre_llm_call` gets the turn's user message as Hermes built it (`agent/turn_context.py:806-821`, `:1131`). Before storing it, the plugin removes every block Hermes added. Each one is listed with its source line in [`provenance_gate/capture.py`](provenance_gate/capture.py):
  - the Discord routing note
  - the reply pointer `[Replying to …: "…"]` (`gateway/run_inbound.py:1562`)
  - `[The user sent …]` notes for documents, images, audio, video and voice
  - inlined attachment text `[Content of NAME]:` and everything after it
  - `--- Attached Context ---` blocks from `@` references
  - shared-chat sender labels and channel backfill
  - skill bodies inlined by a skill command, keeping only the user's own instruction
- **Tests.** `tests/test_hermes_strings.py` builds these blocks with Hermes' own functions and checks that they are removed.
- **Empty results.** A message made only of such blocks stores nothing.
- **Sender.** Each row stores the turn's `sender_id`.
- **Agents that are not the user.** The curator fork (`platform="curator"`, `agent/curator.py:1119`), `delegate_task` children (`platform="subagent"`, `tools/delegate_tool.py:282`) and cron runs (`platform="cron"`, `cron/scheduler.py:2524`) fire `pre_llm_call` with text the user did not type. None of it is captured. Writes from those agents are still checked.

## Threat model

- **What it catches.** The plugin catches an agent that misremembers or invents a user decision and writes it into memory or a skill.
- **What it is not.** It is not a security boundary against a model that is deliberately trying to evade it:
  - Wording outside the patterns is not seen.
  - The generic-actor rule exempts code blocks, table rows and key/value lines.
  - Anything that can write files under `HERMES_HOME`, for example the `terminal` or `write_file` tools, can edit or forge `said.jsonl`.
- **Group chats.** In group or shared chats, any participant's words can serve as a quote. The evidence row names the sender (`sender_id`).

## What it does not do

- **It is a tripwire, not a complete filter.** A decision worded outside the patterns ("we're going with Friday, per our chat") is not detected.
- **No fuzzy, semantic or stemmed matching.** A paraphrase is not evidence, and shared key words must match exactly: "lint" does not match "linter", "GPU" does not match "GPUs". A Chinese quote for an English claim shares no tokens with it and is refused.
- **The quote lives in the written text.** Hermes tool arguments cannot carry evidence on the side. A `pre_tool_call` `modify` return can only add or override keys (`hermes_cli/plugins.py:2006-2013`), and the memory executor passes only its named arguments to the tool (`agent/inline_tool_executors.py:142-149`). So the user's words appear in `MEMORY.md` or the skill file next to the claim.
- **A match shows that the words were typed, not what they answered.**
- **Only the `memory` and `skill_manage` tools are checked.** Writes to memory or skill files through `terminal`, `write_file` or `patch`, and external memory providers, are not checked.
- **Some typed text is lost.** In a message with an inlined text attachment, the typed text that follows the `[Content of …]` block is dropped, because the boundary cannot be found reliably. A voice transcript that Hermes prepends as `"…"` is kept: it cannot be told apart from typed text, and it is the user's own speech.

## Privacy

- **What is stored.** The plugin keeps the text the user typed (not images or audio) and its `sender_id` in `<HERMES_HOME>/plugin-data/provenance-gate/said.jsonl`. Evidence rows, which include the quote, the claim and the sender, go in `evidence.jsonl` in the same folder. It is profile-aware (`plugins/plugin_storage.py:27`).
- **File permissions.** On POSIX, both files are created with mode 0600. On Windows the mode bits are ignored, and the files inherit the access rules of the folder, normally your user profile.
- **Retention.** Rows older than `PROVENANCE_GATE_RETENTION_DAYS` (default 30) are ignored when read. They are also removed from both files, at most once an hour, when a new row is written. `hermes provenance-gate purge --older-than DAYS` or `--all` deletes rows on demand.
- **Nothing leaves the machine.** The plugin makes no network requests.

## Related

- **memory-shield** controls who may change an agent's memory.
- **hermes-dreaming** corroborates remembered facts against what people said, in a nightly pass.
- **provenance-gate** checks claims about user decisions at the moment they are written.

## Tests

```bash
python -m pip install pytest
python -m pytest -v                 # unit tests and corpora; Hermes tests skip without Hermes
```

The end-to-end and real-string tests run when Hermes is importable, for example from a Hermes virtualenv:

```bash
git clone https://github.com/NousResearch/hermes-agent && cd hermes-agent
git checkout b4170e73b0bbd74b47dc567c36166d749c069699
uv venv -p 3.14 && uv pip install -e . --group dev
cd /path/to/provenance-gate && PROVENANCE_GATE_REQUIRE_E2E=1 /path/to/hermes-agent/.venv/bin/python -m pytest -v
```

Corpora (`tests/test_corpora.py`, run with `-s` to print the counts):

- **`tests/corpus/legit.jsonl`:** 213 English and Chinese memory and skill lines that are not decision claims. They include:
  - environment facts, conventions and preferences
  - negated and open decisions
  - quoted code, and YAML, JSON and Markdown
  - non-user subjects
  - generic-actor text: QA and UX notes, UI-step tables, key/value lines, code comments, and Chinese e-commerce and policy rules

  Each is sent as a `memory` add and as a `skill_manage` create, for 426 calls. Blocked: 0.
- **`tests/corpus/claims.jsonl`:** 174 decision-claim cases with their expected outcome. They cover:
  - valid, missing, unsaid and unrelated quotes
  - quotes from another session, under each search setting
  - short quotes
  - several claims in one write
  - claims hidden in quote marks, inch marks and backticks
  - the user's own quoted words
  - every argument shape (string, empty, single-object and unreadable `operations`)
  - the `skill_manage` create, patch, write_file and legacy shapes

  Expected outcome met: 174 of 174.

CI (`.github/workflows/ci.yml`) has three parts:

- **Unit tests and corpora** on Python 3.11 to 3.14, on Ubuntu and Windows.
- **The `hermes-tools` job.** Hermes at the pinned commit on Python 3.14, all tests including end-to-end and real-string tests, `hermes plugins validate` (no warnings) and `hermes plugins doctor provenance-gate --ci`.
- **A weekly scheduled run** of the same job against Hermes `main`.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
