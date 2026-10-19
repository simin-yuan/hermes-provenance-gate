"""What a patch produces, so a claim assembled from existing text is seen.

A ``memory`` patch replaces one span inside one entry, and a ``skill_manage`` patch replaces a
span inside a skill file. Checking only the replacement misses a claim built out of the text
around it ("The user is evaluating Postgres" + patch "is evaluating" -> "chose"), or a patch
that removes the quote from an existing quoted claim. The resolver below reproduces the result
the way the tools compute it and returns the text to check:

- memory: the whole patched entry. Entries are ``"\\n§\\n"``-separated in
  ``<HERMES_HOME>/memories/MEMORY.md`` / ``USER.md`` (tools/memory_tool_store.py:25 and :299); the
  entry is the one containing ``old_text``, an exact whole-entry match first
  (``MemoryStore._locate``), and the span is replaced once (``_patch_entry``, :146-159).
- skill: every paragraph of the patched file that contains a replaced span. The skill is found with
  the public ``tools.skills_tool.skill_search_dirs`` / ``locate_skill`` (documented for plugins in
  website/docs/developer-guide/plugins/index.md, "Read the skill catalog"); ``file_path`` defaults
  to ``SKILL.md`` and must stay inside the skill directory.

When the result cannot be reproduced (no Hermes, file missing, span not found) the resolver
returns None and the caller checks the replacement text alone, as before; the tool itself then
fails on a span it cannot find.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ENTRY_DELIMITER = "\n§\n"
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")


def patched_entry(entries: list[str], old_text: str, new_text: str) -> str | None:
    """The entry after replacing ``old_text`` once, choosing the entry like MemoryStore._locate."""
    if not old_text:
        return None
    stripped = old_text.strip()
    index = next((i for i, e in enumerate(entries) if e.strip() == stripped), None)
    if index is None:
        index = next((i for i, e in enumerate(entries) if old_text in e), None)
    if index is None:
        return None
    return entries[index].replace(old_text, new_text, 1)


def patched_paragraphs(original: str, old: str, new: str, replace_all: bool = False) -> str | None:
    """The paragraphs of the patched text that contain a replaced span (joined by blank lines)."""
    if not old or old not in original:
        return None
    pieces, spans, last, pos = [], [], 0, original.find(old)
    while pos != -1:
        pieces.append(original[last:pos])
        start = sum(map(len, pieces)) + sum(len(new) for _ in spans)
        spans.append((start, start + len(new)))
        last = pos + len(old)
        if not replace_all:
            break
        pos = original.find(old, last)
    pieces.append(original[last:])
    result = new.join(pieces)
    breaks = [(m.start(), m.end()) for m in _PARAGRAPH_BREAK.finditer(result)]
    chosen: list[tuple[int, int]] = []
    for start, end in spans:
        left = max((b_end for b_start, b_end in breaks if b_end <= start), default=0)
        right = min((b_start for b_start, b_end in breaks if b_start >= end), default=len(result))
        if (left, right) not in chosen:
            chosen.append((left, right))
    return "\n\n".join(result[a:b] for a, b in sorted(chosen))


class DiskResolver:
    """Reads the current memory and skill files through Hermes' public helpers."""

    def memory_entry_after(self, target: str, old_text: str, new_text: str) -> str | None:
        try:
            from hermes_constants import get_hermes_home
        except ImportError:
            return None
        path = Path(get_hermes_home()) / "memories" / ("USER.md" if target == "user" else "MEMORY.md")
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return None
        entries = [e.strip() for e in raw.replace("\r\n", "\n").split(ENTRY_DELIMITER)]
        return patched_entry([e for e in entries if e], old_text, new_text)

    def skill_text_after(self, name: str, file_path: str | None, old: str, new: str,
                         replace_all: bool) -> str | None:
        try:
            from tools.skills_tool import locate_skill, skill_lookup_path_error, skill_search_dirs
        except ImportError:
            return None
        if not name or skill_lookup_path_error(name):
            return None
        roots, _active = skill_search_dirs()
        error, skill_dir, _skill_md = locate_skill(name, None, roots)
        if error or skill_dir is None:
            return None
        relative = file_path or "SKILL.md"
        target = (Path(skill_dir) / relative).resolve()
        root = Path(skill_dir).resolve()
        if os.path.commonpath([str(target), str(root)]) != str(root):
            return None
        try:
            original = target.read_text(encoding="utf-8").replace("\r\n", "\n")
        except OSError:
            return None
        return patched_paragraphs(original, old.replace("\r\n", "\n"), new.replace("\r\n", "\n"), replace_all)
