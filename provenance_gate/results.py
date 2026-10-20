"""What a write produces, so a claim assembled from existing text is seen.

A ``memory`` patch replaces one span inside one entry, and a ``skill_manage`` patch replaces a span
inside a skill file. Checking only the replacement misses a claim built out of the text around it
("The user is evaluating Postgres" + patch "is evaluating" -> "chose"), or a patch that removes the
quote from an existing quoted claim. ``args.added_texts`` replays a call's operations in order over
the current files (so an add followed by a patch in the same call is seen too) and checks:

- memory: the whole patched entry. Entries are ``"\\n§\\n"``-separated in
  ``<HERMES_HOME>/memories/MEMORY.md`` / ``USER.md`` (tools/memory_tool_store.py:25 and :299); the
  entry is the one containing ``old_text``, an exact whole-entry match first
  (``MemoryStore._locate``), and the span is replaced once (``_patch_entry``, :146-159).
- skill: every line of the patched file that a replaced span touches. The skill is found with the
  public ``tools.skills_tool.skill_search_dirs`` / ``locate_skill`` (documented for plugins in
  website/docs/developer-guide/plugins/index.md, "Read the skill catalog"); ``file_path`` defaults to
  ``SKILL.md`` and must stay inside the skill directory. Hermes applies skill patches with fuzzy
  matching (tools/fuzzy_match.py ``fuzzy_find_and_replace``); when the exact span is absent, the span
  is looked up again with whitespace collapsed and Unicode / quote marks normalized, which covers the
  whitespace- and unicode-normalized strategies. Block-anchor matches are not reproduced: then only
  the replacement text is checked.

A sentence that is already present, unchanged, in the stored text before the call is not checked
again: fixing a typo in an entry that carries an earlier quoted decision, or a consolidation that
re-states it, does not need the old quote to be in the current session.
"""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path

ENTRY_DELIMITER = "\n§\n"

_QUOTE_FOLD = str.maketrans({"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'"})


def patched_entry(entries: list[str], old_text: str, new_text: str) -> tuple[int, str] | None:
    """``(index, entry after the patch)``, choosing the entry like MemoryStore._locate."""
    if not old_text:
        return None
    stripped = old_text.strip()
    index = next((i for i, e in enumerate(entries) if e.strip() == stripped), None)
    if index is None:
        index = next((i for i, e in enumerate(entries) if old_text in e), None)
    if index is None:
        return None
    return index, entries[index].replace(old_text, new_text, 1)


def _normalized_with_map(text: str) -> tuple[str, list[int]]:
    """``text`` with whitespace runs collapsed, NFKC applied, invisible characters dropped and curly
    quotes folded, plus for every output character the index of the input character it came from."""
    from .quotes import detection_form
    out: list[str] = []
    where: list[int] = []
    in_space = False
    for i, ch in enumerate(text):
        piece = detection_form(ch).translate(_QUOTE_FOLD)
        for c in piece:
            if c.isspace():
                if in_space or not out:
                    continue
                in_space = True
                c = " "
            else:
                in_space = False
            out.append(c)
            where.append(i)
    return "".join(out), where


def _spans(original: str, old: str, replace_all: bool) -> list[tuple[int, int]]:
    """Where ``old`` sits in ``original``: exact first, then whitespace/Unicode-normalized."""
    spans: list[tuple[int, int]] = []
    pos = original.find(old)
    if pos != -1:
        while pos != -1:
            spans.append((pos, pos + len(old)))
            if not replace_all:
                break
            pos = original.find(old, pos + len(old))
        return spans
    norm_text, where = _normalized_with_map(original)
    norm_old = _normalized_with_map(old)[0].strip()
    if not norm_old:
        return spans
    pos = norm_text.find(norm_old)
    while pos != -1:
        start = where[pos]
        end = where[pos + len(norm_old) - 1] + 1
        spans.append((start, end))
        if not replace_all:
            break
        pos = norm_text.find(norm_old, pos + len(norm_old))
    return spans


def patch_text(original: str, old: str, new: str, replace_all: bool = False) -> tuple[str, str] | None:
    """``(patched text, the lines a replacement touches)`` or None when ``old`` is not found."""
    if not old:
        return None
    spans = _spans(original, old, replace_all)
    if not spans:
        return None
    pieces, touched, last = [], [], 0
    for start, end in spans:
        pieces.append(original[last:start])
        offset = sum(map(len, pieces)) + len(new) * len(touched)
        touched.append((offset, offset + len(new)))
        last = end
    pieces.append(original[last:])
    result = new.join(pieces)
    chosen: list[tuple[int, int]] = []
    for start, end in touched:
        left = result.rfind("\n", 0, start) + 1
        if end > start and result[end - 1] == "\n":
            end -= 1  # a replacement ending in a newline does not touch the next line
        right = result.find("\n", end)
        right = len(result) if right == -1 else right
        if (left, right) not in chosen:
            chosen.append((left, right))
    return result, "\n".join(result[a:b] for a, b in sorted(chosen))


def patched_lines(original: str, old: str, new: str, replace_all: bool = False) -> str | None:
    """The lines of the patched text that a replaced span touches (see ``patch_text``)."""
    done = patch_text(original, old, new, replace_all)
    return None if done is None else done[1]


class DiskResolver:
    """Reads the current memory and skill files through Hermes' public helpers."""

    def memory_entries(self, target: str) -> list[str] | None:
        try:
            from hermes_constants import get_hermes_home
        except ImportError:
            return None
        path = Path(get_hermes_home()) / "memories" / ("USER.md" if target == "user" else "MEMORY.md")
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return None
        return [e.strip() for e in raw.replace("\r\n", "\n").split(ENTRY_DELIMITER) if e.strip()]

    def skill_file(self, name: str, file_path: str | None) -> str | None:
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
        root = Path(skill_dir).resolve()
        target = (root / (file_path or "SKILL.md")).resolve()
        try:
            if os.path.commonpath([str(target), str(root)]) != str(root):
                return None
            return target.read_text(encoding="utf-8").replace("\r\n", "\n")
        except (OSError, ValueError):
            return None
