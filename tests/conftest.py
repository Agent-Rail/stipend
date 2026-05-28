"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

SAMPLE_POLICY_YAML = """version: 1
agent: test-agent
limits:
  per_transaction_cap: { USD: 25_000_00 }
  daily_cap:           { USD: 250_000_00 }
  monthly_cap:         { USD: 1_000_000_00 }
recipients:
  allowed: ["acme-logistics", "carrier-*"]
  blocked: ["sanctioned-*"]
approvals:
  requires_approval_above: { USD: 10_000_00 }
"""


@pytest.fixture
def policy_path(tmp_path: Path) -> Path:
    """A valid sample policy.yaml in a tmp dir."""
    p = tmp_path / "policy.yaml"
    p.write_text(SAMPLE_POLICY_YAML, encoding="utf-8")
    return p


@pytest.fixture
def stipend_root(tmp_path: Path) -> Path:
    """A tmp .stipend/ directory."""
    root = tmp_path / ".stipend"
    root.mkdir()
    return root
