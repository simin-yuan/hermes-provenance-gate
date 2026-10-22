"""Which repository, if any, a session actually worked in.

A claim about a code change is worth more when it carries the revision it was made at. The gate can
see tool calls, so it can notice ``git -C <repo>`` and ``workdir=<repo>`` — but noticing *a* repository
is not the same as knowing *the* repository:

* Most claims have none. "The user decided on Postgres" is not about a checkout.
* When a session touches two repositories, the write is about one of them, and the gate cannot tell
  which. Picking either produces a commit that will later be read as a hard fact — worse than an
  empty field, because nobody re-checks something that looks verified.

So a revision is pinned only when the session touched exactly one repository and that repository
still answers ``git rev-parse HEAD``. Everything else yields an empty string, on purpose.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path

from . import store

logger = logging.getLogger(__name__)

REPOS_FILE = "session_repos.json"
MAX_SESSIONS = 32
MAX_REPOS_PER_SESSION = 8
_GIT_TIMEOUT_SECONDS = 4

_PATH_KEYS = frozenset({"workdir", "cwd", "path", "file_path", "directory", "dir", "repo", "repo_path"})
_GIT_C = re.compile(r"""git\s+-C\s+(?:"([^"]+)"|'([^']+)'|([^\s;&|]+))""")
_CD = re.compile(r"""(?:^|[;&|]\s*)cd\s+(?:"([^"]+)"|'([^']+)'|([^\s;&|]+))""")
_SHA = re.compile(r"^[0-9a-f]{7,40}$")


def _normalize(path: str) -> str | None:
    """Absolute native path, or ``None`` for anything we would be guessing about.

    MSYS-style ``/c/Users/...`` is what this shell hands to tools, so it is translated; relative
    paths are dropped outright, because their meaning depends on a working directory we do not have.
    """
    text = (path or "").strip().strip("\"'")
    if not text or text in (".", "..") or text.startswith("-"):
        return None
    match = re.match(r"^/([a-zA-Z])/(.*)$", text)
    if match:
        text = f"{match.group(1).upper()}:/{match.group(2)}"
    if not (os.path.isabs(text) or re.match(r"^[a-zA-Z]:[\\/]", text)):
        return None
    return os.path.normpath(text)


def _repo_root(path: str) -> str | None:
    """Walk up from a path to the nearest directory holding ``.git`` (a file in a worktree counts)."""
    current = Path(path)
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return str(candidate)
    return None


def _candidates(args) -> set[str]:
    found: set[str] = set()
    stack = [args]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if isinstance(value, str) and key in _PATH_KEYS:
                    found.add(value)
                elif isinstance(value, (dict, list)):
                    stack.append(value)
                elif isinstance(value, str) and ("git -C" in value or "cd " in value):
                    stack.append(value)
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, str):
            for regex in (_GIT_C, _CD):
                for groups in regex.findall(item):
                    found.update(group for group in groups if group)
    return found


def load(directory: Path) -> dict:
    try:
        data = json.loads((directory / REPOS_FILE).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save(directory: Path, data: dict) -> None:
    path = directory / REPOS_FILE
    with store.locked(path):
        trimmed = dict(sorted(data.items(), key=lambda item: item[1].get("ts", ""), reverse=True)[:MAX_SESSIONS])
        path.write_text(json.dumps(trimmed, ensure_ascii=False, indent=1), encoding="utf-8")


def record(directory: Path, session_id: str, args) -> None:
    """Remember every repository this session's tool calls clearly refer to. Never raises."""
    if not session_id:
        return
    try:
        repos = set()
        for path in _candidates(args):
            absolute = _normalize(path)
            if not absolute:
                continue
            # The path itself need not exist yet (a file about to be written, a directory about to
            # be created); what matters is whether some ancestor is a checkout.
            root = _repo_root(absolute)
            if root:
                repos.add(root)
        if not repos:
            return
        data = load(directory)
        entry = data.get(session_id) or {"repos": []}
        merged = sorted(set(entry.get("repos") or []) | repos)[:MAX_REPOS_PER_SESSION]
        if merged == entry.get("repos"):
            return
        data[session_id] = {"repos": merged, "ts": store.format_ts(store.utc_now())}
        _save(directory, data)
    except Exception:  # noqa: BLE001 - bookkeeping must never disturb a tool call
        logger.debug("provenance-gate: could not record a touched repository", exc_info=True)


def touched(directory: Path, session_id: str) -> list[str]:
    entry = load(directory).get(session_id) or {}
    return list(entry.get("repos") or [])


def head_sha(repo: str) -> str:
    """``git -C <repo> rev-parse HEAD`` as lowercase hex, or ``""`` when it cannot be read."""
    try:
        out = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=_GIT_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 - a missing git binary is "no revision", not an error
        return ""
    sha = (out.stdout or "").strip().lower()
    return sha if out.returncode == 0 and _SHA.match(sha) else ""


def pin(directory: Path, session_id: str) -> str:
    """The commit to record on a claim: a sha when the session touched exactly one repository, else ''."""
    repos = touched(directory, session_id)
    if len(repos) != 1:
        return ""
    return head_sha(repos[0])


__all__ = ["REPOS_FILE", "record", "touched", "head_sha", "pin"]
