from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import timedelta

import pytest

from provenance_gate import capture, store
from provenance_gate.hooks import Gate


def test_message_text_string_and_parts():
    assert capture.message_text("hello") == "hello"
    parts = [{"type": "text", "text": "first"}, {"type": "image_url", "image_url": {"url": "data:..."}},
             {"type": "input_audio", "input_audio": {}}, {"type": "text", "text": "second"}]
    assert capture.message_text(parts) == "first\nsecond"
    assert capture.message_text(None) == ""
    assert capture.message_text({"text": "not a list"}) == ""


def test_capture_row_shape(tmp_path):
    now = store.utc_now()
    row = capture.capture(tmp_path, "sess-1", "I decided we ship on Friday", now=now)
    ts = store.format_ts(now)
    assert row == {"ts": ts, "session_id": "sess-1", "text": "I decided we ship on Friday",
                   "msg_id": hashlib.sha256(("sess-1" + ts + "I decided we ship on Friday").encode()).hexdigest()[:16]}
    assert ts.endswith("Z")
    assert capture.said_rows(tmp_path) == [row]


def test_capture_skips_empty(tmp_path):
    assert capture.capture(tmp_path, "s", "   \n") is None
    assert capture.capture(tmp_path, "s", [{"type": "image_url", "image_url": {}}]) is None
    assert capture.said_rows(tmp_path) == []


def test_dedup_within_two_seconds(tmp_path):
    t0 = store.utc_now()
    assert capture.capture(tmp_path, "s", "same text", now=t0)
    assert capture.capture(tmp_path, "s", "same text", now=t0 + timedelta(seconds=1.5)) is None
    assert capture.capture(tmp_path, "other", "same text", now=t0 + timedelta(seconds=1.6))  # other session
    assert capture.capture(tmp_path, "s", "different", now=t0 + timedelta(seconds=1.7))
    assert capture.capture(tmp_path, "s", "same text", now=t0 + timedelta(seconds=3))  # outside the window
    assert len(capture.said_rows(tmp_path)) == 4


def test_retention_drops_old_rows_at_most_hourly(tmp_path):
    t0 = store.utc_now()
    capture.capture(tmp_path, "s", "old message", now=t0, retention_days=30)
    capture.capture(tmp_path, "s", "newer message", now=t0 + timedelta(days=20), retention_days=30)
    # The last prune ran at t0 + 20 days, so this append prunes again: the first row is now
    # older than 30 days.
    later = t0 + timedelta(days=31)
    capture.capture(tmp_path, "s", "third", now=later, retention_days=30)
    assert [r["text"] for r in capture.said_rows(tmp_path)] == ["newer message", "third"]
    # Within the hour after a prune, nothing is pruned even if rows expired.
    capture.capture(tmp_path, "s", "fourth", now=later + timedelta(minutes=30), retention_days=1)
    assert len(capture.said_rows(tmp_path)) == 3
    capture.capture(tmp_path, "s", "fifth", now=later + timedelta(minutes=61), retention_days=1)
    assert [r["text"] for r in capture.said_rows(tmp_path)] == ["third", "fourth", "fifth"]


def test_retention_env(monkeypatch):
    monkeypatch.delenv("PROVENANCE_GATE_RETENTION_DAYS", raising=False)
    assert store.retention_days() == 30
    monkeypatch.setenv("PROVENANCE_GATE_RETENTION_DAYS", "7")
    assert store.retention_days() == 7
    for bad in ("0", "-3", "seven", ""):
        monkeypatch.setenv("PROVENANCE_GATE_RETENTION_DAYS", bad)
        assert store.retention_days() == 30


def test_file_lock_parallel_threads(tmp_path):
    workers, per_worker = 8, 40
    errors: list[BaseException] = []

    def work(index: int) -> None:
        try:
            for j in range(per_worker):
                capture.capture(tmp_path, f"s{index}", f"message {index}-{j} " + "x" * 2000)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    raw = (tmp_path / store.SAID_FILE).read_bytes()
    lines = raw.split(b"\n")
    assert lines[-1] == b""
    rows = [json.loads(line) for line in lines[:-1]]  # every line is complete JSON
    assert len(rows) == workers * per_worker
    assert len({r["text"] for r in rows}) == workers * per_worker


def test_lock_excludes_concurrent_holder(tmp_path):
    path = tmp_path / "x.jsonl"
    order: list[str] = []
    entered = threading.Event()

    def holder() -> None:
        with store.locked(path):
            entered.set()
            order.append("holder-start")
            threading.Event().wait(0.2)
            order.append("holder-end")

    t = threading.Thread(target=holder)
    t.start()
    entered.wait()
    with store.locked(path):
        order.append("main")
    t.join()
    assert order == ["holder-start", "holder-end", "main"]


def test_rows_are_utf8_with_lf(tmp_path):
    capture.capture(tmp_path, "s", "用户说：周五发布\r\nok")
    raw = (tmp_path / store.SAID_FILE).read_bytes()
    assert b"\r\n" not in raw and raw.endswith(b"\n")
    assert "周五发布".encode("utf-8") in raw
    assert capture.said_rows(tmp_path)[0]["text"] == "用户说：周五发布\r\nok"


def test_unreadable_lines_are_skipped(tmp_path):
    path = tmp_path / store.SAID_FILE
    path.write_bytes(b'{"ts":"2026-01-01T00:00:00Z","session_id":"s","text":"ok"}\nnot json\n\xff\xfe\n[1,2]\n')
    assert [r["text"] for r in capture.said_rows(tmp_path)] == ["ok"]


@pytest.mark.parametrize("platform", sorted(capture.NON_USER_PLATFORMS))
def test_non_user_platforms_are_not_captured(tmp_path, platform):
    gate = Gate(directory=lambda: tmp_path)
    assert gate.pre_llm_call(session_id="s", user_message="I decided we ship on Friday", platform=platform) is None
    assert capture.said_rows(tmp_path) == []


def test_pre_llm_call_captures_and_returns_nothing(tmp_path):
    gate = Gate(directory=lambda: tmp_path)
    assert gate.pre_llm_call(session_id="s", user_message="hello there", platform="cli", model="m",
                             conversation_history=[], is_first_turn=True) is None
    assert [r["text"] for r in capture.said_rows(tmp_path)] == ["hello there"]


def test_pre_llm_call_fails_open(tmp_path):
    def broken():
        raise OSError("disk gone")
    assert Gate(directory=broken).pre_llm_call(session_id="s", user_message="hi") is None


def test_data_dir_without_hermes(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    try:
        import plugins.plugin_storage  # noqa: F401
    except ImportError:
        assert store.data_dir() == tmp_path / "plugin-data" / "provenance-gate"
        assert store.data_dir().is_dir()
    else:  # inside a Hermes venv the public helper is used
        assert store.data_dir().name == "provenance-gate"


def test_lock_file_is_not_the_data_file(tmp_path):
    with store.locked(tmp_path / "said.jsonl"):
        pass
    assert (tmp_path / "said.jsonl.lock").exists()
    assert not (tmp_path / "said.jsonl").exists()
    assert os.path.getsize(tmp_path / "said.jsonl.lock") == 0
