"""Keep a local copy of what the user typed, one row per turn, in ``said.jsonl``.

``pre_llm_call`` receives the turn's user message as Hermes built it (agent/turn_context.py:1131,
:815). Besides what the user typed, that text can carry blocks Hermes wrote. Each is removed
before the row is stored (strings as built at Hermes b4170e73):

- the Discord routing note ``[Triggering message id: `…` — use as `message_id` …]``
  (gateway/run_inbound.py:50-55, prepended at :1575);
- the reply pointer ``[Replying to your previous message: "…"]`` / ``[Replying to: "…"]``
  (gateway/run_inbound.py:1562);
- attachment notes ``[The user sent …]`` for documents, images, audio, video and voice
  (gateway/run.py:2540-2559, gateway/run_inbound.py:1500-1520 and :1797-1840,
  gateway/run_inbound_transcription.py:34) and the untranscribed / empty-voice notes (:43, :156-161,
  :179-183);
- inlined attachment text ``[Content of NAME]:`` and everything after it (the adapters, e.g.
  plugins/platforms/discord/adapter.py:6049, slack/adapter.py:4848, telegram/adapter.py:6788,
  feishu/adapter.py:3090, whatsapp/adapter.py:998) and the ``[Slack attachment notice]`` block
  (slack/adapter.py:4890);
- ``@`` reference expansions ``--- Attached Context ---`` / ``--- Context Warnings ---``
  (agent/context_references.py:244-246);
- the shared-chat sender label ``[Name] `` and channel backfill before ``[New message]``
  (gateway/run_inbound.py:1400-1403);
- skill invocation bodies, keeping only the user's own instruction (agent/skill_commands.py
  markers :49-70, builders :366-418 and :816-826, gateway auto-load gateway/run_turn_prepare.py:484-503; the CLI sends them from cli.py:1342);
- quoted-reply blocks of the WeChat and QQ adapters, ``[引用: …]`` / ``[引用媒体…]`` followed by a
  newline (plugins/platforms/weixin/adapter.py:536-544) and ``[Quoted message]:`` followed by the
  quoted lines and a blank line (plugins/platforms/qqbot/adapter.py:876-890). The quoted text can
  contain anything, so everything up to the LAST ``]\n`` / blank line is removed: losing some typed
  words is safe, keeping quoted words is not;
- the model-switch note ``[Note: model was just switched …]`` (gateway/slash_commands_model.py:239-247);
- leading system notes Hermes puts before the user's new message, ``[System note: …]`` /
  ``[System: …]`` followed by a blank line (gateway/run.py:1039-1079 resume note,
  gateway/run_turn_runner.py:891 interrupted-turn note).

Hermes also sends whole turns that the user did not type. A turn whose remaining text starts with
one of these headers is not captured at all (``SYNTHETIC_TURN_PREFIXES``): Hermes' own list of
"scaffolding user rows that carry no real user words" (agent/context_compressor.py:937-940) —
``[System:``, ``[CONTEXT``, ``[PRIOR CONTEXT``, ``[IMPORTANT: Background``, ``[Your active task
list``, ``[Planning state preserved``, ``[ASYNC DELEGATION``, ``[OUT-OF-BAND``, ``Cronjob
Response:`` — plus background-process notices (tools/process_registry_notifications.py:463-510:
``[IMPORTANT: …]``, ``[Background process … heartbeat``), async delegation results
(:280-318, ``[ASYNC DELEGATION COMPLETE — …]``), /goal and kanban continuation prompts
(hermes_cli/goals.py:60-110 and :1592, ``[Continuing toward …``), the goal kick (:979,
``[Goal set]``), heartbeat prompts (hermes_cli/heartbeat.py:22-26, ``[Heartbeat — …``), any
other ``[System note:`` turn, and cron / webhook deliveries (cron/scheduler_delivery.py:191,
gateway/platforms/webhook.py:1058). Kinds not listed here may still be captured.

A message made only of such blocks stores nothing. Removing too much only means fewer words can
serve as evidence; nothing here can add text the user did not type.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import store

DEDUP_SECONDS = 2.0

# Agents whose ``user_message`` is not typed by the user. Hermes builds these with a fixed
# ``platform``: the curator fork (agent/curator.py:1119 ``platform="curator"``), delegate_task
# children (tools/delegate_tool.py:282 ``platform="subagent"``, the goal is written by the parent
# model) and cron runs (cron/scheduler.py:2524 ``platform="cron"``).
NON_USER_PLATFORMS = frozenset({"curator", "subagent", "cron"})

_EMPTY_PLACEHOLDER = "(The user sent a message with no text content)"
_DISCORD_NOTE = re.compile(r"\A\[Triggering message id: `[^`\n]*` — use as `message_id` for reply/react/pin "
                           r"via the discord tools\.\](?:\n\n|\Z)")
_REPLY_START = re.compile(r'\A\[Replying to(?: your previous message)?: "')
_REPLY_END = '"]\n\n'
_SENT_NOTE = re.compile(r"\A\[(?:The user sent |voice message could not be transcribed)[^\n]*\](?:\n\n|\Z)")
_IMAGE_NOTE_START = "[The user sent an image~ Here's what I can see:\n"
_IMAGE_NOTE_END = re.compile(r"\[If you need a closer look, use vision_analyze with image_url: [^\n]* ~\](?:\n\n|\Z)")
_STT_EMPTY = re.compile(r"\A\[[^\n\]]*empty or inaudible[^\n]*\](?:\n\n|\Z)")
_SLACK_NOTICE = re.compile(r"\A\[Slack attachment notice\]\n(?:- [^\n]*(?:\n|\Z))*\n?")
_SENDER_LABEL = re.compile(r"\A\[[^\]\n]{1,200}\] ")
_NEW_MESSAGE = "\n\n[New message]"
_CONTENT_OF = re.compile(r"(?:\A|\n)\[Content of [^\n]*\]:\n")
_CONTEXT_BLOCKS = ("\n\n--- Attached Context ---\n\n", "\n\n--- Context Warnings ---\n")

# agent/skill_commands.py markers (byte-identical to the builders).
_SKILL_INVOCATION_PREFIX = "[IMPORTANT: The user has invoked the "
_SINGLE_SKILL_INSTRUCTION = "The user has provided the following instruction alongside the skill invocation: "
_RUNTIME_NOTE = "\n\n[Runtime note:"
_BUNDLE_MARKER = " skill bundle,"
_BUNDLE_USER_INSTRUCTION = "\nUser instruction: "
_BUNDLE_FIRST_SKILL_BLOCK = "\n\n[Loaded as part of the "
_AUTO_LOAD_PREFIX = '[IMPORTANT: The "'
_SKILL_DIR_NOTE_END = "then run them with the terminal tool using the absolute path."

SYNTHETIC_TURN_PREFIXES = (
    # agent/context_compressor.py:937-940 (_SYNTHETIC_USER_ROW_PREFIXES)
    "[System:", "[CONTEXT", "[PRIOR CONTEXT", "[IMPORTANT: Background", "[Your active task list",
    "[Planning state preserved", "[ASYNC DELEGATION", "[OUT-OF-BAND", "Cronjob Response:",
    # further synthetic turns (see the module docstring)
    "[IMPORTANT:", "[System note:", "[Background process ", "[Continuing toward ", "[Goal set]",
    "[Heartbeat", "[Cron delivery:", "[Webhook delivery:",
)
_WEIXIN_QUOTE = re.compile(r"\A\[引用(?:媒体)?(?::|：|\])")
_QQ_QUOTE = "[Quoted message]:"
_MODEL_NOTE = re.compile(r"\A\[Note: model was just switched [^\]]*\]\s*")
_LEADING_SYSTEM_NOTE = re.compile(r"\A\[System(?: note)?:[^\]]*\]\n\n(?=\S)")


def message_text(user_message: Any) -> str:
    """Text of a user message: a string as is; for a list of content parts, the text parts
    joined with ``\\n`` (images, audio and other non-text parts are ignored)."""
    if isinstance(user_message, str):
        return user_message
    if not isinstance(user_message, list):
        return ""
    parts = []
    for part in user_message:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and part.get("type", "text") in ("text", "input_text"):
            text = part.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _skill_instruction(text: str) -> str | None:
    """The user's instruction inside a skill scaffold ("" when there is none), or None when
    ``text`` is not a skill scaffold."""
    if text.startswith(_SKILL_INVOCATION_PREFIX):
        if _BUNDLE_MARKER in text.split("\n", 1)[0]:
            start = text.find(_BUNDLE_USER_INSTRUCTION)  # header precedes the bodies: FIRST marker
            if start == -1:
                return ""
            start += len(_BUNDLE_USER_INSTRUCTION)
            end = text.find(_BUNDLE_FIRST_SKILL_BLOCK, start)
            return text[start:end if end != -1 else len(text)].strip()
        start = text.rfind(_SINGLE_SKILL_INSTRUCTION)  # the instruction follows the body: LAST marker
        if start == -1:
            return ""
        start += len(_SINGLE_SKILL_INSTRUCTION)
        end = text.find(_RUNTIME_NOTE, start)
        return text[start:end if end != -1 else len(text)].strip()
    if text.startswith(_AUTO_LOAD_PREFIX) and '" skill is auto-loaded' in text.split("\n", 1)[0]:
        end = text.rfind(_SKILL_DIR_NOTE_END)
        if end == -1:
            return ""
        tail = text[end + len(_SKILL_DIR_NOTE_END):].lstrip()
        # Trailing scaffold blocks after the footer (agent/skill_commands.py:337).
        while tail.startswith(("[Skill config (", "[Skill setup note: ", "[This skill has supporting files")):
            cut = tail.find("\n\n")
            tail = tail[cut + 2:].lstrip() if cut != -1 else ""
        return tail.strip()
    return None


def typed_text(text: str) -> str:
    """``text`` with every block Hermes injected removed (see the module docstring)."""
    text = text.replace("\r\n", "\n")
    for marker in _CONTEXT_BLOCKS:
        cut = text.find(marker)
        if cut != -1:
            text = text[:cut]
    content = _CONTENT_OF.search(text)
    if content:
        text = text[:content.start()]
    text = text.rstrip()
    for _ in range(64):  # peel prefixes, outermost first, until none is left
        before = text
        text = _LEADING_SYSTEM_NOTE.sub("", text, count=1)  # a note before the user's new message
        instruction = _skill_instruction(text)
        if instruction is not None:
            text = instruction  # a skill scaffold: keep only the user's own instruction
            continue
        if text.lstrip().startswith(SYNTHETIC_TURN_PREFIXES):
            return ""  # a turn Hermes wrote, not the user
        text = _DISCORD_NOTE.sub("", text, count=1)
        text = _MODEL_NOTE.sub("", text, count=1)
        if _WEIXIN_QUOTE.match(text):
            cut = text.rfind("]\n")
            text = text[cut + 2:] if cut != -1 else ""
        if text.startswith(_QQ_QUOTE):
            cut = text.rfind("\n\n")
            text = text[cut + 2:] if cut != -1 else ""
        if _REPLY_START.match(text):
            cut = text.rfind(_REPLY_END)
            text = text[cut + len(_REPLY_END):] if cut != -1 else ""
        if text.startswith(_IMAGE_NOTE_START):
            ends = list(_IMAGE_NOTE_END.finditer(text))
            text = text[ends[-1].end():] if ends else ""
        text = _SENT_NOTE.sub("", text, count=1)
        text = _STT_EMPTY.sub("", text, count=1)
        text = _SLACK_NOTICE.sub("", text, count=1)
        cut = text.rfind(_NEW_MESSAGE)
        if cut != -1 and text[cut + len(_NEW_MESSAGE):cut + len(_NEW_MESSAGE) + 1] in ("\n", ""):
            text = text[cut + len(_NEW_MESSAGE) + 1:]
        text = _SENDER_LABEL.sub("", text, count=1)
        if text == before:
            break
    text = text.strip()
    if text == _EMPTY_PLACEHOLDER or text.startswith(SYNTHETIC_TURN_PREFIXES):
        return ""
    return text


def message_id(session_id: str, ts: str, text: str) -> str:
    return hashlib.sha256((session_id + ts + text).encode("utf-8")).hexdigest()[:16]


def _is_duplicate(rows: list[dict], session_id: str, text: str, now: datetime) -> bool:
    for row in reversed(rows):
        moment = store.parse_ts(row.get("ts"))
        if moment is None:
            continue
        if abs((now - moment).total_seconds()) > DEDUP_SECONDS:
            if moment < now:
                return False  # rows are appended in time order; older ones cannot match
            continue
        if row.get("session_id") == session_id and row.get("text") == text:
            return True
    return False


def capture(directory: Path, session_id: str, user_message: Any, now: datetime | None = None,
            retention_days: int | None = None, sender_id: str = "", parent_session_id: str = "") -> dict | None:
    """Append the turn's typed text to ``said.jsonl``; returns the row, or None when skipped
    (nothing typed, or the same text for the same session within two seconds). A session started by
    context compression carries ``parent_session_id``; it is stored with the row (and alone, with
    empty text, once per session when the turn has no typed text) so quotes from before
    compression stay in reach (verdict.Evidence)."""
    text = typed_text(message_text(user_message))
    link = bool(parent_session_id) and parent_session_id != session_id
    if not text and not link:
        return None
    now = now or store.utc_now()
    session_id = session_id or ""
    path = directory / store.SAID_FILE
    with store.locked(path):
        store.maybe_prune_unlocked(path, now, retention_days or store.retention_days())
        rows = store.read_rows_unlocked(path)
        if not text and any(r.get("session_id") == session_id and r.get("parent_session_id") == parent_session_id
                            for r in rows):
            return None  # nothing typed and the compression link is already recorded
        if text and _is_duplicate(rows, session_id, text, now):
            return None
        ts = store.format_ts(now)
        row = {"ts": ts, "session_id": session_id, "sender_id": sender_id or "",
               "msg_id": message_id(session_id, ts, text), "text": text}
        if parent_session_id and parent_session_id != session_id:
            row["parent_session_id"] = parent_session_id
        store.append_rows_unlocked(path, [row])
    return row


def said_rows(directory: Path, lock: bool = True, now: datetime | None = None,
              retention_days: int | None = None) -> list[dict]:
    """Captured rows within retention, oldest first. Rows past retention are ignored here even
    before the next prune removes them. ``lock=False`` reads without creating the lock file."""
    path = directory / store.SAID_FILE
    rows = store.read_rows(path) if lock else store.read_rows_unlocked(path)
    cutoff = (now or store.utc_now()) - timedelta(days=retention_days or store.retention_days())
    return [row for row in rows if isinstance(row.get("text"), str)
            and (moment := store.parse_ts(row.get("ts"))) is not None and moment >= cutoff]
