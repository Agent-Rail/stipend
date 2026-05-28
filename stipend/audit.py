"""Append-only JSONL audit log.

Design decisions from /plan-eng-review:

- **1B**: writes use ``O_APPEND`` plus ``fsync`` per line so concurrent CLI
  and MCP server processes can write to the same file without torn lines or
  lost records. The kernel guarantees that a single ``write()`` of less than
  ``PIPE_BUF`` (4 KiB on Linux/macOS) to an ``O_APPEND`` file descriptor is
  atomic. We pad each JSONL record with a trailing newline and assume entries
  stay below that limit; if a record ever approaches the limit we'd switch
  to a length-prefixed log, but in v0.1 that case is far away.

- **1C**: reads are unindexed and linear; the policy engine scans the whole
  file for daily / monthly window queries. Acceptable at v0.1 throughput.

- **D3 / T9**: the ``prompt`` field is optional. ``--no-prompt`` on the CLI
  and ``prompt=None`` in the SDK omit it. The :func:`prune` helper removes
  entries older than a cutoff.

- **T11**: ``fsync`` failures (disk full, read-only fs) are caught and
  re-raised as :class:`AuditWriteError` so the caller knows the payment was
  NOT executed and exits non-zero rather than crashing.

The on-disk schema is versioned in the entry itself (``audit_schema_version``)
so a future log rotation or migration can identify and upgrade old entries.
"""

from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

AUDIT_SCHEMA_VERSION = 1


def _empty_extra() -> dict[str, Any]:
    """Typed factory for AuditEntry.extra (gives pyright a real signature)."""
    return {}


class AuditWriteError(Exception):
    """Raised when an audit-log write fails (disk full, fsync error, etc.).

    The caller must treat the underlying transaction as **not executed**.
    Stipend's CLI prints a clear error message and exits non-zero; the SDK
    propagates the exception to the caller.
    """


@dataclass(frozen=True)
class AuditEntry:
    """One entry in the audit log.

    Fields:
        ts: ISO-8601 UTC timestamp with second precision.
        agent: Agent identifier from the policy file.
        prompt: Originating natural-language request. ``None`` when omitted
            (``--no-prompt`` on the CLI, ``prompt=None`` from the SDK, or
            direct SDK call where no prompt exists).
        tool: Which tool produced the entry (``pay``, ``charge``, ``refund``,
            ``policy_check``).
        args: The arguments passed to the tool, normalized for storage.
        policy_decision: ``allow``, ``deny``, or ``requires_approval``.
        backend: Backend module identifier (``mock``, ``agentrail``).
        receipt_id: Backend-issued receipt identifier, if any.
        status: Lifecycle status at the time the entry was written.
        error: Error message string, if any.
        parent_audit_id: For refunds and retries, the audit-id of the parent
            entry. ``None`` for top-level entries.
        audit_schema_version: Always :data:`AUDIT_SCHEMA_VERSION` in v0.1.
        id: Stipend-issued audit identifier (``aud_...``).
    """

    id: str
    ts: str
    agent: str
    tool: str
    args: dict[str, Any]
    policy_decision: str
    backend: str
    status: str
    audit_schema_version: int = AUDIT_SCHEMA_VERSION
    prompt: str | None = None
    receipt_id: str | None = None
    error: str | None = None
    parent_audit_id: str | None = None
    extra: dict[str, Any] = field(default_factory=_empty_extra)

    def to_jsonl(self) -> str:
        """Serialize to a single JSONL line including trailing newline."""
        payload: dict[str, Any] = {
            "audit_schema_version": self.audit_schema_version,
            "id": self.id,
            "ts": self.ts,
            "agent": self.agent,
            "prompt": self.prompt,
            "tool": self.tool,
            "args": self.args,
            "policy_decision": self.policy_decision,
            "backend": self.backend,
            "receipt_id": self.receipt_id,
            "status": self.status,
            "error": self.error,
            "parent_audit_id": self.parent_audit_id,
        }
        if self.extra:
            payload["extra"] = self.extra
        return json.dumps(payload, separators=(",", ":"), sort_keys=False) + "\n"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AuditEntry:
        """Build an AuditEntry from a parsed JSONL line.

        Tolerates older schema versions where some fields may be missing.
        """
        return cls(
            id=str(raw.get("id", "")),
            ts=str(raw.get("ts", "")),
            agent=str(raw.get("agent", "")),
            prompt=raw.get("prompt"),
            tool=str(raw.get("tool", "")),
            args=dict(raw.get("args") or {}),
            policy_decision=str(raw.get("policy_decision", "")),
            backend=str(raw.get("backend", "")),
            receipt_id=raw.get("receipt_id"),
            status=str(raw.get("status", "")),
            error=raw.get("error"),
            parent_audit_id=raw.get("parent_audit_id"),
            audit_schema_version=int(raw.get("audit_schema_version", 1)),
            extra=dict(raw.get("extra") or {}),
        )


class AuditLog:
    """Append-only JSONL audit log at ``<root>/audit.jsonl``."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "audit.jsonl"

    # ------------------------------------------------------------------ write

    def append(self, entry: AuditEntry) -> None:
        """Append one entry to the log atomically.

        Opens the file with ``O_APPEND`` so the OS guarantees that the write
        is appended to the current end-of-file even if another process appended
        in between our seek and write. Calls ``fsync`` after each write so
        readers in other processes see the data immediately and so a power
        loss does not lose more than the in-flight record.

        On any OS error, raises :class:`AuditWriteError` and does NOT swallow
        the cause. Callers must treat the transaction as not executed.
        """
        line = entry.to_jsonl().encode("utf-8")
        try:
            fd = os.open(
                self.path,
                os.O_WRONLY | os.O_APPEND | os.O_CREAT,
                0o644,
            )
            try:
                written = os.write(fd, line)
                if written != len(line):
                    raise AuditWriteError(
                        f"short write to {self.path}: wrote {written} of {len(line)}"
                    )
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as exc:
            raise AuditWriteError(
                f"audit log write failed: {exc}; transaction NOT executed"
            ) from exc

    # ------------------------------------------------------------------- read

    def read_all(self) -> list[AuditEntry]:
        """Read every entry in the log.

        Malformed lines (e.g. truncated writes from a prior crash, manual
        edits) are skipped with a one-time warning rather than crashing.
        """
        if not self.path.exists():
            return []
        entries: list[AuditEntry] = []
        warned = False
        with self.path.open("r", encoding="utf-8") as fp:
            for lineno, raw in enumerate(fp, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    if not warned:
                        # We deliberately do not raise here; the audit log is
                        # the source of truth but a single garbled line must
                        # not stop policy enforcement on the rest.
                        import warnings

                        warnings.warn(
                            f"audit log line {lineno} malformed; skipping",
                            stacklevel=2,
                        )
                        warned = True
                    continue
                entries.append(AuditEntry.from_dict(parsed))
        return entries

    def search(self, filter: dict[str, Any]) -> list[AuditEntry]:
        """Linear scan with filtering.

        Accepted filter keys: ``agent`` (exact), ``status`` (exact),
        ``recipient`` (exact or fnmatch glob), ``ts_after`` (ISO-8601 string),
        ``ts_before`` (ISO-8601 string). Unrecognized keys raise
        :class:`ValueError`.
        """
        recognized = {"agent", "status", "recipient", "ts_after", "ts_before"}
        unknown = set(filter) - recognized
        if unknown:
            raise ValueError(
                f"unrecognized filter key(s): {sorted(unknown)}; "
                f"accepted keys: {sorted(recognized)}"
            )

        results: list[AuditEntry] = []
        for entry in self.read_all():
            if "agent" in filter and entry.agent != filter["agent"]:
                continue
            if "status" in filter and entry.status != filter["status"]:
                continue
            if "recipient" in filter:
                pattern = str(filter["recipient"])
                recipient = str(entry.args.get("recipient", ""))
                if not fnmatch.fnmatchcase(recipient, pattern):
                    continue
            if "ts_after" in filter and entry.ts <= str(filter["ts_after"]):
                continue
            if "ts_before" in filter and entry.ts >= str(filter["ts_before"]):
                continue
            results.append(entry)
        return results

    def tail(self, n: int) -> list[AuditEntry]:
        """Return the last ``n`` entries in order, oldest first."""
        if n <= 0:
            return []
        all_entries = self.read_all()
        return all_entries[-n:]

    # ------------------------------------------------------------------ prune

    def prune(self, older_than: timedelta) -> int:
        """Remove entries older than ``older_than`` from now.

        Returns the number of entries removed. Rewrites the file in place by
        writing a sibling temp file and atomically renaming it; the rename
        is atomic on POSIX so a crash mid-prune leaves either the old or new
        file in place, never a partial.
        """
        if not self.path.exists():
            return 0
        cutoff = datetime.now(UTC) - older_than
        cutoff_iso = cutoff.isoformat(timespec="seconds").replace("+00:00", "Z")

        all_entries = self.read_all()
        kept = [e for e in all_entries if e.ts >= cutoff_iso]
        removed = len(all_entries) - len(kept)
        if removed == 0:
            return 0

        tmp_path = self.path.with_suffix(".jsonl.tmp")
        # Build the new content as bytes so we write once and fsync once.
        body = b"".join(e.to_jsonl().encode("utf-8") for e in kept)
        try:
            fd = os.open(
                tmp_path,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o644,
            )
            try:
                os.write(fd, body)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp_path, self.path)
        except OSError as exc:
            # Try to clean up the temp file on failure; ignore further errors.
            import contextlib

            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise AuditWriteError(f"audit log prune failed: {exc}") from exc

        return removed


def now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string with second precision."""
    return (
        datetime.now(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
