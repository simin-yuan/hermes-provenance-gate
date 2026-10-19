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


def _memory_texts(op: dict, where: str, target: str, resolver) -> list[str]:
    action = op.get("action")
    if action not in MEMORY_TEXT_ACTIONS:
        return []
    texts = _texts(op, ("content", "new_text"), where)
    # The tool writes content, falling back to new_text when content is empty (tools/memory_tool.py).
    texts = texts[:1]
    old_text = op.get("old_text")
    if action == "patch" and texts and isinstance(old_text, str) and old_text and resolver is not None:
        entry = resolver.memory_entry_after(target, old_text, texts[0])
        if entry:
            return [entry]  # the whole patched entry, not just the new span (results.py)
    return texts


def _skill_texts(op: dict, action, fields: tuple[str, ...], where: str, name: str, resolver) -> list[str]:
    texts = _texts(op, fields, where)
    old = op.get("old_string")
    if action == "patch" and isinstance(old, str) and old and resolver is not None:
        new = op.get("new_string")
        new = new if isinstance(new, str) else ""
        file_path = op.get("file_path") if isinstance(op.get("file_path"), str) else None
        after = resolver.skill_text_after(name, file_path, old, new, bool(op.get("replace_all")))
        if after is not None:
            full = [t for t in _texts(op, ("content",), where)]
            return ([after] if after.strip() else []) + full
    return texts


def added_texts(tool_name: str, args: Any, resolver=None) -> list[tuple[str, str]]:
    """``(target, text)`` for every piece of text the call would add. Removals add nothing.

    memory: ``add`` / ``patch`` write ``content`` (alias ``new_text``), as a single op, or as each
    item of ``operations`` when it is truthy (an empty list runs the flat fields, as the tool does).
    skill_manage: ``create`` / ``edit`` -> ``content``; ``patch`` -> ``new_string`` and/or a full
    ``content``; ``write_file`` -> ``file_content``; ``operations`` when present, else the legacy
    flat shape. Raises ``ArgError`` for a shape that cannot be read.

    With a ``resolver`` (results.DiskResolver inside Hermes), a patch contributes what it produces:
    the whole patched memory entry, or the patched skill paragraphs.
    """
    if tool_name not in ("memory", "skill_manage"):
        return []
    if not isinstance(args, dict):
        raise ArgError(f"arguments are not an object ({type(args).__name__})")
    out: list[tuple[str, str]] = []
    if tool_name == "memory":
        target = args.get("target")
        target = target if isinstance(target, str) else "memory"
        ops = coerce_operations(args.get("operations"))
        if ops:
            for index, op in enumerate(ops, 1):
                out += [(f"memory:{target}", t) for t in _memory_texts(op, f"operation {index}", target, resolver)]
        else:
            out += [(f"memory:{target}", t) for t in _memory_texts(args, "memory", target, resolver)]
        return out
    ops = coerce_operations(args.get("operations"))
    default_name = args.get("name") if isinstance(args.get("name"), str) else ""
    for index, op in enumerate(ops if ops is not None else [args], 1):
        action = op.get("action")
        name = op.get("name") if isinstance(op.get("name"), str) else default_name
        fields = SKILL_TEXT_FIELDS.get(action, ())
        out += [(f"skill:{name}", t) for t in _skill_texts(op, action, fields, f"operation {index}", name, resolver)]
    return out
