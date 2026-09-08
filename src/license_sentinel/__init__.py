"""license-sentinel - audit Python and npm dependency licenses before you ship.

An MCP server that reads what is actually on your disk and judges it against how
*you* distribute your product. Works for Python and npm in one pass, runs
locally over stdio, and imports no HTTP client anywhere: no network calls, no
telemetry, nothing leaves your machine.

The public surface is deliberately small:

    from license_sentinel import audit_project, check_package, generate_notices
"""

from __future__ import annotations

from .collectors import Dependency, collect
from .licenses import (
    CONTEXTS,
    Category,
    Context,
    Finding,
    License,
    Verdict,
    classify,
    evaluate,
    normalize_context,
)
from .report import AuditResult, audit, render_audit, render_notices, write_notices

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "audit",
    "audit_project",
    "check_package",
    "generate_notices",
    "AuditResult",
    "Category",
    "Context",
    "CONTEXTS",
    "Dependency",
    "collect",
    "classify",
    "evaluate",
    "normalize_context",
    "Finding",
    "License",
    "render_audit",
    "render_notices",
    "Verdict",
    "write_notices",
]


def __getattr__(name: str):
    """Expose the tools lazily so importing the package never needs the MCP SDK."""
    if name in {"audit_project", "check_package", "generate_notices"}:
        from . import server

        return getattr(server, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
