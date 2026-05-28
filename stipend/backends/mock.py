"""Mock backend.

A local, deterministic-ish backend that mimics a payment lifecycle without
any network calls. Persists payment state to a SQLite database at
``<root>/state.db`` so audit log searches stay coherent across CLI
invocations and an MCP server session running in parallel.

Per /plan-eng-review decisions:

- **1B**: SQLite is opened with WAL journaling and a busy timeout so
  concurrent writers from CLI and MCP processes do not collide.
- **1D** lifecycle: each process instantiates one MockBackend; durable state
  lives in SQLite, not in the instance.
- **T11**: settlement is **synchronous-with-delay**, not threaded. The
  call to ``pay`` blocks briefly (``simulated_eta_seconds``) and returns a
  ``status="settled"`` receipt directly. This eliminates the orphan-pending
  failure mode flagged in the failure-modes table.

The simulated rail label is ``MOCK_ACH`` and the flat rail fee is 25 cents.
Counterparty IDs are deterministic per ``(recipient,)`` so demos are
reproducible.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from stipend.backends.base import Backend
from stipend.receipt import Receipt

MOCK_RAIL_FEE_CENTS = 25
"""Flat simulated rail fee applied to every settled mock payment."""

MOCK_RAIL_LABEL = "MOCK_ACH"

DEFAULT_SIMULATED_ETA_SECONDS = 2.4


class MockBackend(Backend):
    """The default backend. Persists to SQLite with WAL journaling."""

    name = "mock"

    def __init__(
        self,
        state_dir: str | Path,
        *,
        simulated_eta_seconds: float = DEFAULT_SIMULATED_ETA_SECONDS,
        sleep: bool = False,
    ) -> None:
        """Initialize the mock backend.

        Args:
            state_dir: Directory holding ``state.db``. Created if missing.
            simulated_eta_seconds: How long settlement claims to take. The
                value is shown in the rendered trace; we do not actually
                sleep for it during tests.
            sleep: When ``True``, actually sleep for ``simulated_eta_seconds``
                between create and settle. Useful for the CLI demo where the
                ETA should be perceptible; tests pass ``sleep=False``.
        """
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.state_dir / "state.db"
        self.simulated_eta_seconds = simulated_eta_seconds
        self.sleep = sleep
        self._init_db()

    # --------------------------------------------------------------- schema

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
        # WAL gives concurrent readers across processes (decision 1B).
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS payments (
                    id TEXT PRIMARY KEY,
                    recipient TEXT NOT NULL,
                    counterparty_id TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL,
                    net_cents INTEGER,
                    currency TEXT NOT NULL,
                    memo TEXT NOT NULL,
                    rail TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    settled_at TEXT
                )
                """
            )

    # ------------------------------------------------------------------ ops

    def pay(
        self,
        *,
        recipient: str,
        amount_cents: int,
        currency: str,
        memo: str,
    ) -> Receipt:
        if amount_cents <= 0:
            raise ValueError(f"amount_cents must be positive; got {amount_cents}")
        return self._settle_payment(
            recipient=recipient,
            amount_cents=amount_cents,
            currency=currency,
            memo=memo,
            prefix="pmt",
        )

    def charge(
        self,
        *,
        source: str,
        amount_cents: int,
        currency: str,
        memo: str,
    ) -> Receipt:
        if amount_cents <= 0:
            raise ValueError(f"amount_cents must be positive; got {amount_cents}")
        return self._settle_payment(
            recipient=source,
            amount_cents=amount_cents,
            currency=currency,
            memo=memo,
            prefix="chg",
        )

    def refund(
        self,
        *,
        payment_id: str,
        amount_cents: int | None,
    ) -> Receipt:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT recipient, counterparty_id, amount_cents, currency, memo "
                "FROM payments WHERE id = ?",
                (payment_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"unknown payment_id: {payment_id}")
        recipient, counterparty_id, original_cents, currency, memo = row
        refund_cents = original_cents if amount_cents is None else int(amount_cents)
        rid = self._next_id("rfd")
        now = _utcnow()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO payments(id, recipient, counterparty_id, amount_cents, "
                "net_cents, currency, memo, rail, status, created_at, settled_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rid,
                    recipient,
                    counterparty_id,
                    refund_cents,
                    refund_cents,
                    currency,
                    f"Refund of {payment_id}: {memo}",
                    MOCK_RAIL_LABEL,
                    "settled",
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        return Receipt(
            id=rid,
            status="settled",
            recipient=recipient,
            amount_cents=refund_cents,
            currency=currency,
            memo=f"Refund of {payment_id}",
            rail=MOCK_RAIL_LABEL,
            backend=self.name,
            created_at=now,
            settled_at=now,
            rail_fee_cents=0,
            net_cents=refund_cents,
            counterparty_id=counterparty_id,
        )

    # ---------------------------------------------------------- internals

    def _settle_payment(
        self,
        *,
        recipient: str,
        amount_cents: int,
        currency: str,
        memo: str,
        prefix: str,
    ) -> Receipt:
        counterparty_id = _resolve_counterparty(recipient)
        created_at = _utcnow()
        pid = self._next_id(prefix)

        with self._connect() as conn:
            conn.execute(
                "INSERT INTO payments(id, recipient, counterparty_id, amount_cents, "
                "net_cents, currency, memo, rail, status, created_at, settled_at) "
                "VALUES(?, ?, ?, ?, NULL, ?, ?, ?, 'pending', ?, NULL)",
                (
                    pid,
                    recipient,
                    counterparty_id,
                    amount_cents,
                    currency,
                    memo,
                    MOCK_RAIL_LABEL,
                    created_at.isoformat(),
                ),
            )

        if self.sleep:
            time.sleep(self.simulated_eta_seconds)

        net_cents = amount_cents - MOCK_RAIL_FEE_CENTS
        settled_at = _utcnow()
        with self._connect() as conn:
            conn.execute(
                "UPDATE payments SET status='settled', net_cents=?, settled_at=? "
                "WHERE id = ?",
                (net_cents, settled_at.isoformat(), pid),
            )

        return Receipt(
            id=pid,
            status="settled",
            recipient=recipient,
            amount_cents=amount_cents,
            currency=currency,
            memo=memo,
            rail=MOCK_RAIL_LABEL,
            backend=self.name,
            created_at=created_at,
            settled_at=settled_at,
            rail_fee_cents=MOCK_RAIL_FEE_CENTS,
            net_cents=net_cents,
            counterparty_id=counterparty_id,
        )

    @staticmethod
    def _next_id(prefix: str) -> str:
        return f"{prefix}_mock_{secrets.token_urlsafe(8)}"


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _resolve_counterparty(recipient: str) -> str:
    """Deterministically resolve a recipient string to a mock counterparty id."""
    digest = hashlib.sha256(recipient.encode("utf-8")).hexdigest()
    return f"{recipient.split('-')[0]}_{digest[:8]}"
