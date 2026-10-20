"""What a ``memory`` / ``skill_manage`` call would add, read the way Hermes will execute it.

``pre_tool_call`` sees the arguments before Hermes coerces them (tools/arg_coercion.py
``coerce_tool_args``, applied in agent/inline_tool_executors.py:305 and model_tools.py:889). This
module mirrors that coercion for ``operations``: a JSON string is decoded, a single object is
wrapped in a list, and JSON-encoded items are decoded. Anything it cannot read raises
``ArgError`` so the call is refused (fail closed) instead of passing unchecked.
"""

from __future__ import annotations

import json
from typing import Any

MEMORY_TEXT_ACTIONS = ("add", "patch")
MEMORY_ACTIONS = ("add", "patch", "remove")
SKILL_TEXT_FIELDS = {"create": ("content",), "edit": ("content",), "patch": ("new_string", "content"),
                     "write_file": ("file_content",)}
SKILL_ACTIONS = ("create", "edit", "patch", "delete", "write_file", "remove_file")


class ArgError(ValueError):
    """The arguments cannot be read, so they cannot be checked."""


def coerce_operations(value: Any) -> list | None:
    """Hermes' coercion of an array argument: None stays None; a JSON string is decoded (it must
    decode to a list); a dict becomes a one-item list; JSON-encoded items are decoded."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise ArgError("operations is a string that is not valid JSON") from None
        if not isinstance(value, list):
            raise ArgError("operations is a JSON string that does not decode to a list")
    elif isinstance(value, dict):
        value = [value]
    elif isinstance(value, tuple):
        value = list(value)
    elif not isinstance(value, list):
        raise ArgError(f"operations has an unreadable type ({type(value).__name__})")
    items = []
    for index, item in enumerate(value, 1):
        if isinstance(item, str) and item.strip().startswith("{"):
            try:
                item = json.loads(item)
            except ValueError:
                raise ArgError(f"operation {index} is a string that is not valid JSON") from None
        if not isinstance(item, dict):
            raise ArgError(f"operation {index} is not an object ({type(item).__name__})")
        items.append(item)
    return items


def _texts(op: dict, fields: tuple[str, ...], where: str) -> list[str]:
    out = []
    for field in fields:
        value = op.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ArgError(f"{where}: {field} is not a string ({type(value).__name__})")
        if value:
            out.append(value)
    return out


def added_texts(tool_name: str, args: Any, resolver=None) -> list[tuple[str, str, str]]:
    """``(target, text, before)`` for every piece of text the call would add; ``before`` is stored
    text the piece replaces or repeats (sentences found in it are not re-checked). Removals add
    nothing.

    memory: ``add`` / ``patch`` write ``content`` (alias ``new_text``), as a single op, or as each
    item of ``operations`` when it is truthy (an empty list runs the flat fields, as the tool does).
    skill_manage: ``create`` / ``edit`` -> ``content``; ``patch`` -> ``new_string`` and/or a full
    ``content``; ``write_file`` -> ``file_content``; ``operations`` when present, else the legacy
    flat shape. Raises ``ArgError`` for a shape that cannot be read.

    With a ``resolver`` (results.DiskResolver inside Hermes), the operations are replayed in order
    over the current files, so a patch contributes what it produces (results.py), including a patch
    of text added earlier in the same call.
    """
    if tool_name not in ("memory", "skill_manage"):
        return []
    if not isinstance(args, dict):
        raise ArgError(f"arguments are not an object ({type(args).__name__})")
    if tool_name == "memory":
        return _memory_added(args, resolver)
    return _skill_added(args, resolver)


def _memory_added(args: dict, resolver) -> list[tuple[str, str, str]]:
    from .results import ENTRY_DELIMITER, patched_entry
    target = args.get("target")
    target = target if isinstance(target, str) else "memory"
    key = f"memory:{target}"
    ops = coerce_operations(args.get("operations"))
    flat = not ops
    ops = [args] if flat else ops
    entries = (resolver.memory_entries(target) if resolver is not None else None) or []
    stored = ENTRY_DELIMITER.join(entries)
    out: list[tuple[str, str, str]] = []
    for index, op in enumerate(ops, 1):
        where = "memory" if flat else f"operation {index}"
        action = op.get("action")
        if action == "remove":
            old = op.get("old_text")
            if isinstance(old, str) and old.strip():
                entries = [e for e in entries if old.strip() not in e]
            continue
        if action not in MEMORY_TEXT_ACTIONS:
            continue
        # The tool writes content, falling back to new_text when content is empty (tools/memory_tool.py).
        texts = _texts(op, ("content", "new_text"), where)[:1]
        if not texts:
            continue
        new = texts[0]
        old = op.get("old_text")
        if action == "patch" and isinstance(old, str) and old:
            done = patched_entry(entries, old, new)
            if done is not None:
                i, after = done
                out.append((key, after, entries[i]))
                entries[i] = after
                continue
            out.append((key, new, ""))
            continue
        out.append((key, new, stored))  # an add: repeating stored text is not a new claim
        entries.append(new.strip())
    return out


def _skill_added(args: dict, resolver) -> list[tuple[str, str, str]]:
    from .results import patch_text
    ops = coerce_operations(args.get("operations"))
    default_name = args.get("name") if isinstance(args.get("name"), str) else ""
    files: dict[tuple[str, str], str | None] = {}

    def current(name: str, path: str) -> str | None:
        if (name, path) not in files:
            files[(name, path)] = resolver.skill_file(name, path) if resolver is not None else None
        return files[(name, path)]

    out: list[tuple[str, str, str]] = []
    for index, op in enumerate(ops if ops is not None else [args], 1):
        where = f"operation {index}"
        action = op.get("action")
        name = op.get("name") if isinstance(op.get("name"), str) else default_name
        key = f"skill:{name}"
        fields = SKILL_TEXT_FIELDS.get(action, ())
        file_path = op.get("file_path") if isinstance(op.get("file_path"), str) and op.get("file_path") else "SKILL.md"
        texts = _texts(op, fields, where)
        if action in ("create", "edit") or (action == "write_file"):
            for t in texts:
                out.append((key, t, current(name, file_path) or ""))
                files[(name, file_path)] = t
            continue
        if action == "patch":
            full = _texts(op, ("content",), where)
            old = op.get("old_string")
            if isinstance(old, str) and old:
                new = op.get("new_string")
                new = new if isinstance(new, str) else ""
                original = current(name, file_path)
                done = None if original is None else patch_text(original, old, new, bool(op.get("replace_all")))
                if done is not None:
                    result, touched = done
                    if touched.strip():
                        out.append((key, touched, original))
                    files[(name, file_path)] = result
                else:
                    out += [(key, t, "") for t in _texts(op, ("new_string",), where)]
            for t in full:
                out.append((key, t, current(name, file_path) or ""))
                files[(name, file_path)] = t
            continue
        if action in ("delete", "remove_file"):
            files[(name, file_path)] = None
    return out
