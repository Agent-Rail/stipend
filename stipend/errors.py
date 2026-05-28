"""Public exception types for Stipend.

These are exported from :mod:`stipend` so callers can ``except`` them by name
without reaching into the internal module layout.
"""


class StipendError(Exception):
    """Base class for all Stipend exceptions."""


class PolicyDenied(StipendError):
    """Raised when the policy engine denies a transaction.

    The :attr:`reason` attribute carries a short, human-readable explanation
    suitable for surfacing to a user or logging into the audit trail.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ApprovalRequired(StipendError):
    """Raised when a transaction exceeds the approval threshold.

    v0.1 returns the decision only; there is no signed-token resubmission flow
    in the package itself. SDK callers catch this exception and resolve it in
    whichever way fits their application (escalate to a human, abort, etc).
    """

    def __init__(self, reason: str, threshold_cents: int, currency: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.threshold_cents = threshold_cents
        self.currency = currency


class NotYetAvailable(StipendError):
    """Raised by the AgentRail backend stub.

    The error string is the user-facing message that points readers at
    https://agentrail.com when they reach for the production-rails backend.
    """


class PolicyConfigError(StipendError):
    """Raised when a policy YAML / dict fails schema or sanity validation."""
