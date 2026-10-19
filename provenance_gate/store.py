"""JSONL files under the plugin's data directory, serialized by a cross-platform file lock.

Every read and write of a file happens while its ``<file>.lock`` is held, so concurrent
hooks (threads or processes) never see a half-written or half-pruned file. Rows are written
as UTF-8 bytes with ``\\n`` endings on every OS.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)

PLUGIN_NAME = "provenance-gate"
SAID_FILE = "said.jsonl"
EVIDENCE_FILE = "evidence.jsonl"
DEFAULT_RETENTION_DAYS = 30
PRUNE_INTERVAL_SECONDS = 3600

_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


def data_dir() -> Path:
    """``<HERMES_HOME>/plugin-data/provenance-gate/``, resolved on every call (profile-aware).

    Inside Hermes this is ``plugins.plugin_storage.plugin_data_dir``. Outside Hermes (unit tests,
    a bare checkout) the same layout is built from ``HERMES_HOME`` or ``~/.hermes``.
    """
    try:
        from plugins.plugin_storage import plugin_data_dir
    except ImportError:
        home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
        root = home / "plugin-data" / PLUGIN_NAME
        root.mkdir(parents=True, exist_ok=True)
        return root
    return plugin_data_dir(PLUGIN_NAME)


def retention_days() -> int:
    """``PROVENANCE_GATE_RETENTION_DAYS`` (a positive integer), else 30."""
    raw = os.environ.get("PROVENANCE_GATE_RETENTION_DAYS", "").strip()
    try:
        days = int(raw)
    except ValueError:
        return DEFAULT_RETENTION_DAYS
    return days if days > 0 else DEFAULT_RETENTION_DAYS


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_ts(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _thread_lock(path: Path) -> threading.Lock:
    key = str(path)
    with _thread_locks_guard:
        return _thread_locks.setdefault(key, threading.Lock())


LOCK_TIMEOUT_SECONDS = 30.0
LOCK_RETRY_SECONDS = 0.05
# errno values a non-blocking lock attempt raises when another holder has it: msvcrt.locking
# (EACCES, EDEADLOCK) and fcntl.flock with LOCK_NB (EWOULDBLOCK / EAGAIN).
_CONTENTION_ERRNOS = frozenset({errno.EACCES, getattr(errno, "EDEADLOCK", 36), getattr(errno, "EDEADLK", 35),
                                errno.EAGAIN, getattr(errno, "EWOULDBLOCK", errno.EAGAIN)})


def lock_with_retry(try_lock: Callable[[], None], timeout: float = LOCK_TIMEOUT_SECONDS,
                    sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
    """Call ``try_lock`` (a non-blocking attempt) until it succeeds. Only lock contention is
    retried, and only until ``timeout`` (then ``TimeoutError``); any other ``OSError`` propagates."""
    deadline = clock() + timeout
    while True:
        try:
            try_lock()
            return
        except OSError as exc:
            if exc.errno not in _CONTENTION_ERRNOS:
                raise
            if clock() >= deadline:
                raise TimeoutError(f"lock still held after {timeout:.0f}s") from exc
            sleep(LOCK_RETRY_SECONDS)


if os.name == "nt":  # pragma: no cover - exercised on the Windows CI runners
    import msvcrt

    def _lock_fd(fd: int) -> None:
        def attempt() -> None:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        lock_with_retry(attempt)

    def _unlock_fd(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_fd(fd: int) -> None:
        lock_with_retry(lambda: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB))

    def _unlock_fd(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def locked(path: Path) -> Iterator[None]:
    """Hold the exclusive lock for ``path`` (in-process and cross-process)."""
    lock_path = path.with_name(path.name + ".lock")
    with _thread_lock(path):
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            _lock_fd(fd)
            try:
                yield
            finally:
                _unlock_fd(fd)
        finally:
            os.close(fd)


def encode_row(row: dict) -> bytes:
    return json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


def _decode_lines(data: bytes) -> list[dict]:
    rows = []
    for line in data.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            logger.debug("provenance-gate: skipping unreadable row in store")
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


# Parsed rows per file, reused while the file only grows: (device, inode, size, rows).
_row_cache: dict[str, tuple[int, int, int, list[dict]]] = {}
_row_cache_guard = threading.Lock()


def read_rows_unlocked(path: Path) -> list[dict]:
    """Every readable row. Rows already parsed are reused: when the file has only grown since the
    last read (same file, larger size), only the new bytes are parsed; a rewrite (prune, purge)
    replaces the file and resets the cache. Callers get a fresh list each time."""
    try:
        with open(path, "rb") as handle:
            st = os.fstat(handle.fileno())
            key = str(path)
            with _row_cache_guard:
                cached = _row_cache.get(key)
            if cached and cached[0] == st.st_dev and cached[1] == st.st_ino and cached[2] <= st.st_size:
                handle.seek(cached[2])
                tail = handle.read()
                # Only whole lines are parsed; a partial last line waits for the next read.
                cut = tail.rfind(b"\n") + 1
                rows = cached[3] + _decode_lines(tail[:cut])
                size = cached[2] + cut
            else:
                data = handle.read()
                cut = data.rfind(b"\n") + 1
                rows = _decode_lines(data[:cut])
                size = cut
                tail = data
            trailing = _decode_lines(tail[cut:]) if cut < len(tail) else []
    except FileNotFoundError:
        return []
    with _row_cache_guard:
        _row_cache[key] = (st.st_dev, st.st_ino, size, rows)
    return list(rows) + trailing


def read_rows(path: Path) -> list[dict]:
    with locked(path):
        return read_rows_unlocked(path)


# New files are created owner-only (0600) on POSIX. On Windows the mode is ignored and the file
# inherits the ACL of the plugin-data folder (normally the user's profile, private to the user).
_FILE_MODE = 0o600


def _open_private(path: Path, flags: int):
    return os.fdopen(os.open(path, flags | getattr(os, "O_BINARY", 0), _FILE_MODE), "wb")


def _ends_mid_line(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                return False
            handle.seek(-1, os.SEEK_END)
            return handle.read(1) != b"\n"
    except FileNotFoundError:
        return False


def append_rows_unlocked(path: Path, rows: list[dict]) -> None:
    """Append rows. If a crash left a torn last line, a newline is written first, so the torn
    fragment stays one unreadable line and cannot swallow the new rows."""
    if not rows:
        return
    prefix = b"\n" if _ends_mid_line(path) else b""
    with _open_private(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT) as handle:
        handle.write(prefix + b"".join(encode_row(row) for row in rows))


def append_rows(path: Path, rows: list[dict]) -> None:
    with locked(path):
        append_rows_unlocked(path, rows)


def rewrite_unlocked(path: Path, rows: list[dict]) -> None:
    """Replace the file atomically: write a temporary file next to it, then rename over it, so a
    crash leaves either the old or the new contents, never a truncated file."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with _open_private(tmp, os.O_WRONLY | os.O_TRUNC | os.O_CREAT) as handle:
            handle.write(b"".join(encode_row(row) for row in rows))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def drop_older_than_unlocked(path: Path, cutoff: datetime) -> int:
    """Remove rows whose ``ts`` is older than ``cutoff`` (or unreadable). Returns how many went."""
    rows = read_rows_unlocked(path)
    kept = [row for row in rows if (moment := parse_ts(row.get("ts"))) is not None and moment >= cutoff]
    if len(kept) != len(rows):
        rewrite_unlocked(path, kept)
    return len(rows) - len(kept)


def maybe_prune_unlocked(path: Path, now: datetime, days: int) -> bool:
    """Apply retention to ``path`` at most once per hour (tracked by a ``.<file>.pruned`` marker)."""
    marker = path.with_name("." + path.name + ".pruned")
    try:
        last = marker.stat().st_mtime
    except FileNotFoundError:
        last = None
    if last is not None and 0 <= now.timestamp() - last < PRUNE_INTERVAL_SECONDS:
        return False
    drop_older_than_unlocked(path, now - timedelta(days=days))
    marker.touch()
    os.utime(marker, (now.timestamp(), now.timestamp()))
    return True
