"""Audit log: append/read/search/prune + concurrent-write atomicity (1B)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from stipend.audit import AuditEntry, AuditLog, AuditWriteError, now_iso


def _make_entry(idx: int, **overrides: object) -> AuditEntry:
    args = {
        "id": f"aud_test_{idx}",
        "ts": now_iso(),
        "agent": "test",
        "tool": "pay",
        "args": {"recipient": f"r-{idx}", "amount_cents": 100, "currency": "USD"},
        "policy_decision": "allow",
        "backend": "mock",
        "status": "settled",
    }
    args.update(overrides)  # type: ignore[arg-type]
    return AuditEntry(**args)  # type: ignore[arg-type]


def test_append_then_read(tmp_path: Path) -> None:
    log = AuditLog(tmp_path)
    log.append(_make_entry(1))
    log.append(_make_entry(2))
    entries = log.read_all()
    assert len(entries) == 2
    assert {e.id for e in entries} == {"aud_test_1", "aud_test_2"}


def test_jsonl_is_well_formed(tmp_path: Path) -> None:
    log = AuditLog(tmp_path)
    log.append(_make_entry(1, prompt="hi"))
    log.append(_make_entry(2, prompt=None))
    raw = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    parsed = [json.loads(line) for line in raw]
    assert parsed[0]["prompt"] == "hi"
    assert parsed[1]["prompt"] is None
    assert all(p["audit_schema_version"] == 1 for p in parsed)


def test_read_empty_log(tmp_path: Path) -> None:
    log = AuditLog(tmp_path)
    assert log.read_all() == []
    assert log.tail(5) == []


def test_search_by_agent(tmp_path: Path) -> None:
    log = AuditLog(tmp_path)
    log.append(_make_entry(1, agent="a"))
    log.append(_make_entry(2, agent="b"))
    assert {e.id for e in log.search({"agent": "a"})} == {"aud_test_1"}


def test_search_by_status(tmp_path: Path) -> None:
    log = AuditLog(tmp_path)
    log.append(_make_entry(1, status="settled"))
    log.append(_make_entry(2, status="denied"))
    assert {e.id for e in log.search({"status": "denied"})} == {"aud_test_2"}


def test_search_by_recipient_glob(tmp_path: Path) -> None:
    log = AuditLog(tmp_path)
    log.append(_make_entry(1))  # recipient=r-1
    log.append(_make_entry(2))  # recipient=r-2
    found = log.search({"recipient": "r-*"})
    assert {e.id for e in found} == {"aud_test_1", "aud_test_2"}


def test_search_unrecognized_key(tmp_path: Path) -> None:
    log = AuditLog(tmp_path)
    log.append(_make_entry(1))
    with pytest.raises(ValueError) as exc:
        log.search({"bogus": "x"})
    assert "bogus" in str(exc.value)


def test_tail(tmp_path: Path) -> None:
    log = AuditLog(tmp_path)
    for i in range(5):
        log.append(_make_entry(i))
    last2 = log.tail(2)
    assert [e.id for e in last2] == ["aud_test_3", "aud_test_4"]


def test_tail_zero_is_empty(tmp_path: Path) -> None:
    log = AuditLog(tmp_path)
    log.append(_make_entry(1))
    assert log.tail(0) == []


def test_prune_removes_old(tmp_path: Path) -> None:
    log = AuditLog(tmp_path)
    # Two recent entries, one stamped in the past.
    log.append(_make_entry(1, ts="2020-01-01T00:00:00Z"))
    log.append(_make_entry(2))
    log.append(_make_entry(3))
    removed = log.prune(timedelta(days=1))
    assert removed == 1
    remaining = log.read_all()
    assert {e.id for e in remaining} == {"aud_test_2", "aud_test_3"}


def test_prune_empty_log_is_noop(tmp_path: Path) -> None:
    log = AuditLog(tmp_path)
    assert log.prune(timedelta(days=1)) == 0


def test_prune_noop_when_nothing_old(tmp_path: Path) -> None:
    log = AuditLog(tmp_path)
    log.append(_make_entry(1))
    assert log.prune(timedelta(days=365)) == 0


def test_malformed_line_skipped(tmp_path: Path) -> None:
    log = AuditLog(tmp_path)
    log.append(_make_entry(1))
    # Append a garbled line by hand.
    with (tmp_path / "audit.jsonl").open("a", encoding="utf-8") as fp:
        fp.write("not valid json\n")
    log.append(_make_entry(2))
    with pytest.warns(UserWarning):
        entries = log.read_all()
    assert len(entries) == 2  # garbled line skipped, valid lines retained


def test_fsync_failure_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Disk-full / fsync failure surfaces as AuditWriteError (T11)."""
    log = AuditLog(tmp_path)

    real_fsync = os.fsync

    def boom(fd: int) -> None:
        raise OSError(28, "no space left on device")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(AuditWriteError):
        log.append(_make_entry(1))
    monkeypatch.setattr(os, "fsync", real_fsync)


def test_concurrent_writers(tmp_path: Path) -> None:
    """Two child processes appending in parallel produce well-formed JSONL (1B)."""
    log_path = tmp_path / "audit.jsonl"
    script = f"""
import os, sys, time
sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})
from stipend.audit import AuditLog, AuditEntry, now_iso

log = AuditLog({str(tmp_path)!r})
prefix = sys.argv[1]
for i in range(50):
    log.append(AuditEntry(
        id=f"aud_" + prefix + "_" + str(i),
        ts=now_iso(),
        agent="test",
        tool="pay",
        args={{"recipient": "r", "amount_cents": 1, "currency": "USD"}},
        policy_decision="allow",
        backend="mock",
        status="settled",
    ))
"""
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", script, p],
            stderr=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
        )
        for p in ("A", "B")
    ]
    try:
        for proc in procs:
            stderr_text = proc.stderr.read() if proc.stderr is not None else b""
            proc.wait(timeout=30)
            assert proc.returncode == 0, stderr_text
    finally:
        for proc in procs:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None and not stream.closed:
                    stream.close()

    # All 100 lines should parse as JSON; no torn writes.
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 100
    parsed = [json.loads(line) for line in lines]
    ids = {p["id"] for p in parsed}
    assert len(ids) == 100  # no collisions, no torn / overlapping records
