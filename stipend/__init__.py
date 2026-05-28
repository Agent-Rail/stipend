"""Stipend: an allowance for your AI agent.

Public surface:

    from stipend import Stipend, Receipt, Policy, AuditEntry, PolicyDecision
    from stipend import NotYetAvailable, ApprovalRequired, PolicyDenied

Everything else is internal and may change without notice.
"""

from stipend.audit import AuditEntry
from stipend.core import Stipend
from stipend.errors import ApprovalRequired, NotYetAvailable, PolicyDenied
from stipend.policy import Policy, PolicyDecision
from stipend.receipt import Receipt

__version__ = "0.1.0"

__all__ = [
    "ApprovalRequired",
    "AuditEntry",
    "NotYetAvailable",
    "Policy",
    "PolicyDecision",
    "PolicyDenied",
    "Receipt",
    "Stipend",
    "__version__",
]
