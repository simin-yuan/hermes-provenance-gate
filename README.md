# provenance-gate

Flags memory and skill writes that claim a user decision the user's own words don't back — and, if you turn it on, refuses them.

`provenance-gate` is a plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

- It keeps a local copy of what the user types each turn (`pre_llm_call`).
- Before a `memory` or `skill_manage` write, it looks for sentences that say the user made a decision, such as "User decided …", "The owner approved …" or "用户决定 …" (`pre_tool_call`).
- Each such sentence needs a quote in "…" that appears verbatim in text the user typed in this session, shares key words with the decision and does not drop a negation.
- **By default (`observe`) nothing is refused:** a write without such a quote goes through and is recorded as `would_block`. `hermes provenance-gate report` lists them.
- **In `block` mode** (`PROVENANCE_GATE_MODE=block`) the write is refused and the model is told how to fix it. Nothing is written, and no human approval is involved.

Why observe is the default: the claim detector is a keyword tripwire. It misses wordings outside its patterns and flags some ordinary text (see [Measured accuracy](#measured-accuracy)). Recording is safe to leave on; refusing writes is a choice to make once you have seen what it flags on your own data.

What is checked is narrower than "the user said it". See [What is checked](#what-is-checked) and the [threat model](#threat-model).

Upstream references below are to Hermes commit
[`b4170e73b0bbd74b47dc567c36166d749c069699`](https://github.com/NousResearch/hermes-agent/tree/b4170e73b0bbd74b47dc567c36166d749c069699).

## Worked example

These are end-to-end tests (`tests/test_e2e_hermes.py`): a real Hermes agent against a fake model server.

**Observe mode (the default), test 7.** The user types `I decided we ship on Friday`; the model saves `User decided to ship on Friday.` with no quote. The write goes through, and `evidence.jsonl` records:

```json
{"ts": "…", "session": "dc9d87bf5db4", "tool": "memory", "target": "memory:memory", "claim": "User decided to ship on Friday.", "quote": null, "msg_id": null, "matched_in": null, "sender_id": null, "verdict": "would_block", "reason": "no_quote"}
```

`hermes provenance-gate report` counts these by reason and lists the newest ones.

**Block mode, test 2.** The user types:

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

A quote that drops a negation is refused (end-to-end test 15, after the user typed "Never delete the prod database without asking me"; the message is shortened here):

```json
{"error": "provenance-gate: this write states a user decision (\"The user approved deleting the prod database (\"delete the prod database\").\") without the user's words. The quoted words (\"delete the prod database\") follow a negation in what the user typed (for example \"never …\" or \"don't …\"), so they do not back the decision. …"}
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
| `PROVENANCE_GATE_MODE` | `observe`: never refuse, log `would_block`. `block`: refuse the write. | `observe` |
| `PROVENANCE_GATE_SEARCH` | `session`: quotes must come from this session. `profile`: if not found in this session, every retained message in the profile is searched too. | `session` |
| `PROVENANCE_GATE_SCOPE` | `decisions`, or `decisions+preferences` to also require quotes for "prefers / likes / 喜欢 / 偏好 …" | `decisions` |
| `PROVENANCE_GATE_RETENTION_DAYS` | Days of stored user text and evidence rows to keep | `30` |

Commands:

```bash
hermes provenance-gate report [--days N]                # counts by verdict and reason, newest flagged claims
hermes provenance-gate log [--limit N] [--verdict pass|blocked|would_block]
hermes provenance-gate check "<text>" [--session ID]   # dry run, writes no rows
hermes provenance-gate purge (--older-than DAYS | --all)
```

`--days`, `--limit` and `--older-than` must be zero or more.

## What is checked

1. **The text a write adds.** Hermes decodes some argument shapes only when it executes the tool (`tools/arg_coercion.py`, applied at `agent/inline_tool_executors.py:305` and `model_tools.py:889`). The plugin decodes them the same way first:
   - `operations` sent as a JSON string is decoded.
   - A single object in `operations` counts as a list of one.
   - Operations sent as JSON strings are decoded.
   - `memory` with an empty `operations` list runs its flat fields, so those are checked.

   Then:
   - `memory`: `content` (or its alias `new_text`) of `add` and `patch` (`tools/memory_tool.py:250-289`, `:373-446`).
   - `skill_manage`: `content` of `create` and the legacy `edit`, `new_string` or `content` of `patch`, `file_content` of `write_file` (`tools/skill_manager_tool.py:722-728`, `:862-919`).
   - **A write is checked by what it produces.** The operations of a call are replayed in order over the current files, so an add followed by a patch in the same call is seen. For a `memory` patch, the whole patched entry is checked: the entry is found and patched the way the store does it (`tools/memory_tool_store.py:146-159`). For a `skill_manage` patch, every line of the patched file that the replacement touches is checked; the skill is found with Hermes' public `skill_search_dirs` / `locate_skill`. Hermes applies skill patches with fuzzy matching (`tools/fuzzy_match.py`); when the exact span is absent the plugin looks it up again with whitespace collapsed and Unicode and quote marks normalized. So "The user is evaluating Postgres" patched to "The user chose Postgres", or a patch that deletes the quote from a quoted claim, is seen. Block-anchor fuzzy matches are not reproduced, and when a span cannot be found at all only the replacement text is checked ([`provenance_gate/results.py`](provenance_gate/results.py)).
   - **Unchanged stored text is not re-checked.** A sentence that is already in the stored entry or file, unchanged, is not checked again, and a quote that is already in the stored text being edited counts as evidence (still subject to the "about the claim" rule). Fixing a typo in an entry that carries an earlier quoted decision, or merging it with another entry, does not need the old quote to be in the current session (`matched_in: "stored-text"`).
   - Removals add nothing.

   If the plugin cannot read the arguments of a `memory` or `skill_manage` call, the call is refused in block mode and logged as `would_block` (`unreadable_arguments`) in observe mode. Examples: an `operations` string that is not JSON, an item that is not an object, a text field that is not a string.
2. **Sentences.** The text is normalized first: NFKC, which also folds full-width letters, with every Unicode Default_Ignorable_Code_Point removed (zero-width and joiner characters, soft hyphen, invisible operators U+2061–2064, U+180E, U+034F, variation selectors, tag characters …) and Markdown `**` / `__` emphasis ignored. It is then split into lines and sentences, in English and Chinese. A hard-wrapped line (indented by one to three spaces, or following a line that stops right after "the user") is joined to the previous one. A sentence end inside a quotation does not split the sentence.
3. **Generic-actor rule.** Not every sentence about "the user" is about the person the agent works for. These are not claims:
   - lines inside a fenced code block or indented by four spaces or a tab, so a `# user confirmed …` inside code is a comment, not a heading. An indented line that starts with a list marker (`    - User decided …`, a nested list item) is prose and is checked
   - Markdown table rows, such as `| User confirmed the order | checkout.js |`
   - key/value lines whose key holds the decision words and whose value is a scalar, such as `user confirmed: false` or `user approved = true`, or a lower-case key with a one-word value (`user chose: dark`). A capitalised prose key with a word value is a claim: `User decided: Postgres.`
   - key/value lines with an identifier key, such as `on_confirm: user confirmed the modal, emit event`
   - QA and spec lines starting with a label such as `Bug #12:`, `Step 3:`, `Expected:` or `Given …`
   - sentences where a study, scenario, flow or example word comes before the subject in the same clause: "In the usability study, the user chose …", "In the checkout flow the user chose …", 测试中, 例如. A contrast starts a new clause: "Usually we wait, but the user approved …" is a claim
   - a source reporting it: "Upstream issue #4411 says the owner approved …", "Docs say the user decided …"; and lines labelled as tickets, interview notes, policies or changelogs (`Ticket PAY-212:`, `CHANGELOG 2.3:`)
   - field and column names: "the user approved column", "the owner decided field"; "signed off with" is not "signed off on"
   - Chinese nouns and noun phrases: 用户确认码, 用户批准率, 用户指示灯, 用户同意书, 老板同意的报销单, 用户确认支付的回调可能重复; data-dictionary lines with `=`; scripts and records (话术, 这是…的记录)
   - Chinese rules and conditions: 用户确认收货后，订单完成; 用户同意隐私政策才能注册
   - a third party or a population: repo owner, 仓库主人, 新用户, and the plural "users"

   All of these are data in [`provenance_gate/patterns.py`](provenance_gate/patterns.py), with tests.
4. **Claims.**
   - **English:** a sentence is a decision claim when its subject is `user`, `the user`, `owner` or `the owner` (optionally with a short parenthetical, "The user (Dana)", and adverbs such as already, explicitly, really, actually, definitely), followed by: decided, chose, agreed, approved, confirmed, signed off, instructed, asked me to, told me to, wants us to, said to, gave the go-ahead, rejected, vetoed, OK'd / okayed, said yes to, greenlit. The phrases "the user's decision: …", "per the user" and "according to the user's instructions" also count.
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
   - **This session.** It comes from this session (`PROVENANCE_GATE_SEARCH=session`, the default). The background review fork carries the parent's session id (`agent/background_review.py:1005`), so its writes are checked against what the user typed in that session. Context compression starts a new session id (`agent/conversation_compression.py:3287`); `pre_llm_call` reports the previous one as `parent_session_id`, the plugin records the link, and the sessions before compression count as this session.
   - **No dropped negation.** If, in the user's message, a negation word comes earlier in the same sentence as the quoted span (up to twelve words, or six CJK characters, back) — never, not, no, don't, without, 不要, 别, 禁止 … — the quote does not count (`quote_negated`), unless the quote itself contains a negation. "Never delete the prod database" and "Do not, under any circumstances, delete the prod database" do not back "approved deleting the prod database". A quote that starts with a negation ("don't approve the refund") does not back an approval-type claim (approved, agreed, confirmed, decided, chose, 同意, 批准 …) that is not itself negated; rejections and instructions are left alone, because their quotes are naturally negative.
   - **Not a question.** A quote from a sentence the user ended with a question mark does not count (`quote_from_question`): "Should we delete the prod database?" asks; it does not approve.
   - **About the claim.** It shares at least two content tokens with the rest of the sentence. Latin tokens are words of three or more letters, lower-cased, minus a short stopword list. CJK tokens are character bigrams.
   - **Short quotes.** A quote shorter than 4 words (Latin) or 4 characters (CJK), such as "yes" or "好", counts only if it is the user's whole message, in this session, among their last 3 messages.

   The quote must be in the same sentence as its claim (`;` ends a sentence). Each claim in a write needs its own quote. Within one sentence, each claim owns the text from its start to the next claim, and its quote must be there: "User decided X ("…") and the user approved Y." fails for the second claim. A write with more than 100 claims is refused (block mode) or flagged.
7. **Unattended runs.** The gate never uses Hermes' approval prompt, so it behaves the same in the CLI, the gateway, cron and unattended runs.
8. **Errors.**
   - The verdict is decided before anything is logged. If the evidence log cannot be written, a warning is logged and the verdict stands.
   - An unreadable `said.jsonl` counts as "nothing typed", so claims fail (refused in block mode, `would_block` in observe mode).
   - An internal error while checking a `memory` or `skill_manage` call refuses the call in block mode, and is logged as `would_block` (`internal_error`) in observe mode.
   - A failure in `pre_llm_call` (capture) is logged and ignored.
9. **Speed.** Claim detection is linear in the size of the write, and at most 101 claims per sentence are examined. Looking up a quote scans the captured messages in the searched scope, so a check costs about (quotes in the write) × (stored text searched). Parsed rows of `said.jsonl` are reused between checks while the file only grows. Tests: 1 MiB of adversarial text is checked in under 2 seconds; a 15 MB store searched in `profile` mode for 100 quoted claims, in under 4 seconds (`tests/test_performance.py`).

## What is captured

- **What is stored.** `pre_llm_call` gets the turn's user message as Hermes built it (`agent/turn_context.py:806-821`, `:1131`). Before storing it, the plugin removes every block Hermes added. Each one is listed with its source line in [`provenance_gate/capture.py`](provenance_gate/capture.py):
  - the Discord routing note
  - the reply pointer `[Replying to …: "…"]` (`gateway/run_inbound.py:1562`)
  - `[The user sent …]` notes for documents, images, audio, video and voice
  - inlined attachment text `[Content of NAME]:` and everything after it
  - `--- Attached Context ---` blocks from `@` references
  - shared-chat sender labels and channel backfill
  - skill bodies inlined by a skill command, keeping only the user's own instruction
  - a leading `[System note: …]` / `[System: …]` before the user's new message (the gateway resume and interrupted-turn notes)
  - quoted-reply blocks: WeChat `[引用: …]` / `[引用媒体…]` (`plugins/platforms/weixin/adapter.py:536-544`) and QQ `[Quoted message]:` (`plugins/platforms/qqbot/adapter.py:876-890`). The quoted text can contain anything, so everything up to the last block boundary is removed: losing some typed words is safe, keeping quoted words is not
  - the model-switch note `[Note: model was just switched …]` (`gateway/slash_commands_model.py:239-247`)
- **Turns Hermes writes itself are not captured at all.** A turn whose text starts with one of these headers stores nothing: Hermes' own list of scaffolding rows "that carry no real user words" (`agent/context_compressor.py:937-940`: `[System:`, `[CONTEXT`, `[PRIOR CONTEXT`, `[IMPORTANT: Background`, `[Your active task list`, `[Planning state preserved`, `[ASYNC DELEGATION`, `[OUT-OF-BAND`, `Cronjob Response:`), background-process notices (`[IMPORTANT: …]`, `[Background process … heartbeat`), async delegation results, `/goal` and kanban continuation prompts (`[Continuing toward …`), the goal kick (`[Goal set]`), heartbeat prompts (`[Heartbeat — …`), other `[System note:` turns, and cron and webhook deliveries. Kinds not listed here may still be captured.
- **Tests.** `tests/test_hermes_strings.py` builds these blocks and turns with Hermes' own functions and checks that they are removed or skipped.
- **Empty results.** A message made only of such blocks stores nothing.
- **Sender and session chain.** Each row stores the turn's `sender_id`, and `parent_session_id` when the session was started by context compression.
- **Agents that are not the user.** The curator fork (`platform="curator"`, `agent/curator.py:1119`), `delegate_task` children (`platform="subagent"`, `tools/delegate_tool.py:282`) and cron runs (`platform="cron"`, `cron/scheduler.py:2524`) fire `pre_llm_call` with text the user did not type. None of it is captured. Writes from those agents are still checked.

## Threat model

- **What it catches.** The plugin catches an agent that misremembers or invents a user decision and writes it into memory or a skill.
- **What it is not.** It is not a security boundary against a model that is deliberately trying to evade it:
  - Wording outside the patterns is not seen.
  - The generic-actor rule exempts code blocks, table rows and key/value lines.
  - Anything that can write files under `HERMES_HOME`, for example the `terminal` or `write_file` tools, can edit or forge `said.jsonl`.
- **Group chats.** In group or shared chats, any participant's words can serve as a quote. The evidence row names the sender (`sender_id`).
- **Other plugins.** Another plugin's `pre_tool_call` `modify` directive can change the arguments after this plugin has checked them.
- **Edge cases.** An empty `session_id` (Hermes always sends one) makes every quote fail in `session` mode. Only the first 32 quoted spans of a sentence are looked up.
- **Compression mid-turn.** If compression starts a new session in the middle of a turn, a write made before the user types again cannot see quotes from before compression in `session` mode (the link is recorded at the next `pre_llm_call`). Use `profile` search if that matters to you.

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
- **Retention.** Rows older than `PROVENANCE_GATE_RETENTION_DAYS` (default 30) are ignored when read. They are also removed from both files, at most once an hour, when a new row is written (the file is rewritten atomically). `hermes provenance-gate purge --older-than DAYS` or `--all` deletes rows on demand. Purging leaves the plugin's empty `.lock` and `.pruned` marker files in the folder; they hold no data.
- **Host isolation.** Under `plugins.isolation: host`, Hermes does not register plugin CLI commands, so `report`, `log`, `check` and `purge` are unavailable; delete the files in the folder by hand to purge.
- **Nothing leaves the machine.** The plugin makes no network requests.

## Measured accuracy

Claim detection was measured on `tests/corpus/blind.jsonl`: 133 realistic memory and skill lines in English and Chinese (49 claims, 84 not), written and labelled by an independent auditor **before** reading any of the plugin's code, patterns or corpora. Before publication five lines were reworded to remove a personal name and details of the author's own setup; their labels are unchanged, and the numbers below, measured after the change, are identical to the original measurement. Nothing else in the file has been edited (its SHA-256 is pinned in `tests/test_corpora.py`). The rules were not tuned on it.

| | Count |
|---|---|
| Claims flagged (true positives) | 34 |
| Claims missed (false negatives) | 15 |
| Non-claims flagged (false positives) | 0 |
| Non-claims passed (true negatives) | 84 |
| **Precision** | **1.000** (34 / 34) |
| **Recall** | **0.694** (34 / 49) |

No ordinary line was flagged, including decisions by other people ("PM decided …", "Board approved …", 产品经理决定 …). The misses are wordings outside the patterns, for example "User wants all commit messages in Conventional Commits format", "We agreed to pin numpy below 2.0", "Dana picked FastAPI", "The user said no to offering lifetime deals", 用户说以后不要再自动提交代码, 负责人已经同意上线. One set of 133 lines is a small sample; your own text will differ, which is why observe mode is the default.

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
- **`tests/corpus/blind.jsonl`:** the independent blind set described in [Measured accuracy](#measured-accuracy).
- **`tests/corpus/audit_regressions.jsonl`:** 100 lines (40 claims, 60 not) built from the shapes the two independent audits reported, labelled by hand. Every label is met. Because the rules were tuned on these shapes, this set is a regression guard, not an accuracy measure; see [Measured accuracy](#measured-accuracy).
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
