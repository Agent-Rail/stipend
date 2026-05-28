"""Backend implementations for Stipend.

In v0.1 there are two backends:

- :class:`stipend.backends.mock.MockBackend` runs locally with a SQLite-backed
  payment lifecycle and synthetic rail fees. It is the default.
- :class:`stipend.backends.agentrail.AgentRailBackend` is a stub that raises
  :class:`stipend.errors.NotYetAvailable` on every payment call. The slot exists
  so the upgrade path to AgentRail's production rails is visible in the code.
"""

from stipend.backends.agentrail import AgentRailBackend
from stipend.backends.base import Backend
from stipend.backends.mock import MockBackend

__all__ = ["AgentRailBackend", "Backend", "MockBackend"]
