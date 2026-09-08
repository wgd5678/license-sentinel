"""The MCP server: three tools and one prompt.

Runs over stdio, so there is nothing to deploy and nothing to keep running.
The tool functions below are plain Python and can be called directly (the
end-to-end check does exactly that); the MCP wiring is a thin layer on top.

MCP SDK 2.x renamed ``FastMCP`` to ``MCPServer``. Most tutorials online still
use the old name and crash on import, so both are probed here.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from .licenses import Verdict, evaluate, normalize_context
from .report import audit, render_audit, write_notices

__all__ = ["audit_project", "check_package", "generate_notices", "pre_release_license_review", "main", "mcp"]

SERVER_NAME = "license-sentinel"


# --------------------------------------------------------------------------
# MCP wiring (optional at import time)
# --------------------------------------------------------------------------

def _load_server_class() -> tuple[Any, str | None]:
    """Return ``(server_class, flavor)`` for whichever SDK is installed."""
    candidates = (
        ("mcp.server.fastmcp", "MCPServer"),  # SDK 2.x
        ("mcp.server.fastmcp", "FastMCP"),  # SDK 1.x
        ("mcp.server", "MCPServer"),
        ("mcp", "FastMCP"),
    )
    for module_name, attr in candidates:
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        klass = getattr(module, attr, None)
        if klass is not None:
            return klass, f"{module_name}.{attr}"
    return None, None


_ServerClass, _FLAVOR = _load_server_class()
mcp = _ServerClass(SERVER_NAME) if _ServerClass is not None else None


def _register(fn):
    """Attach a function to the server when an SDK is available."""
    if mcp is None:
        return fn
    try:
        return mcp.tool()(fn)
    except Exception:  # pragma: no cover - defensive against SDK changes
        return fn


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------


def _error(message: str) -> str:
    return f"error: {message}"


def audit_project(path: str = ".", context: str = "proprietary") -> str:
    """Scan a project's dependencies and report licensing risk.

    Args:
        path: Project directory to scan. Defaults to the current directory.
        context: How you distribute your product. One of
            ``proprietary`` (closed-source product you distribute),
            ``saas-backend`` (you only run it, never distribute),
            ``permissive`` (your own project is MIT/Apache/BSD),
            ``copyleft-ok`` (your own project is GPL family).

    Returns:
        Counts per verdict plus every BLOCK and REVIEW item with the reason.
    """
    try:
        ctx = normalize_context(context)
    except ValueError as exc:
        return _error(str(exc))

    root = Path(path).expanduser()
    if not root.is_dir():
        return _error(f"{path} is not a directory")
    try:
        result = audit(root, ctx)
    except Exception as exc:  # never crash the client on a bad project layout
        return _error(f"could not scan {path}: {exc}")
    return render_audit(result)


def check_package(names: str | list[str], context: str = "proprietary") -> str:
    """Check packages or raw license strings before installing them.

    Args:
        names: Package or license strings to check. Accepts the messy real-world
            forms: ``AGPL-3.0``, ``BUSL-1.1``, ``GPLv3``, ``Apache License 2.0``,
            ``MIT OR Apache-2.0``, or a comma-separated string of any of these.
            Package names are resolved against what is installed in ``path`` when
            possible, otherwise the string itself is classified.
        context: Distribution context, see ``audit_project``.

    Returns:
        One line per input with its verdict and the reason.
    """
    try:
        ctx = normalize_context(context)
    except ValueError as exc:
        return _error(str(exc))

    if isinstance(names, str):
        items = [part.strip() for part in names.replace("\n", ",").split(",")]
    else:
        items = [str(name).strip() for name in names]
    items = [item for item in items if item]
    if not items:
        return _error("no package names given")

    verdicts = {Verdict.BLOCK: [], Verdict.REVIEW: [], Verdict.CLEAN: []}
    lines: list[str] = []
    for item in items:
        verdict, lic, reason = evaluate(item, ctx)
        verdicts[verdict].append(item)
        marker = {Verdict.BLOCK: "[X]", Verdict.REVIEW: "[!]", Verdict.CLEAN: "[ok]"}[verdict]
        lines.append(f"  {marker} {item} -> {lic.id}: {reason}")

    header = [
        f"license check ({ctx.value})",
        f"{len(items)} checked: "
        f"{len(verdicts[Verdict.BLOCK])} BLOCK, "
        f"{len(verdicts[Verdict.REVIEW])} REVIEW, "
        f"{len(verdicts[Verdict.CLEAN])} CLEAN",
        "",
    ]
    return "\n".join(header + lines)


def generate_notices(path: str = ".", output: str = "THIRD-PARTY-NOTICES.md") -> str:
    """Write a THIRD-PARTY-NOTICES.md attribution document for client hand-off.

    Args:
        path: Project directory to scan.
        output: Output file. Relative paths are resolved against ``path``.

    Returns:
        The absolute path written and how many dependencies were listed.
    """
    root = Path(path).expanduser()
    if not root.is_dir():
        return _error(f"{path} is not a directory")
    try:
        target, count = write_notices(root, output)
    except OSError as exc:
        return _error(f"could not write {output}: {exc}")
    return f"wrote {target} ({count} dependencies listed)"


def pre_release_license_review(path: str = ".", context: str = "proprietary") -> str:
    """Prompt: chain a dependency audit into a go/no-go release review."""
    try:
        ctx = normalize_context(context)
    except ValueError as exc:
        return _error(str(exc))
    return (
        "Run a pre-release license review.\n\n"
        f"1. Call audit_project with path={path!r} and context={ctx.value!r}.\n"
        "2. For every BLOCK item, find out whether it can be replaced or "
        "relicensed, and say so explicitly. Do not hand-wave a blocker away.\n"
        "3. For every REVIEW item, state the specific condition that makes it "
        "acceptable (linking model, whether the library was patched, whether you "
        "actually distribute the binary).\n"
        "4. Finish with one line: SHIP, SHIP WITH CONDITIONS, or DO NOT SHIP, "
        "followed by the single most important reason.\n\n"
        "This is not legal advice; flag anything a lawyer should see."
    )


# register whatever the installed SDK understands
audit_project = _register(audit_project)
check_package = _register(check_package)
generate_notices = _register(generate_notices)

if mcp is not None:
    try:
        pre_release_license_review = mcp.prompt()(pre_release_license_review)
    except Exception:  # pragma: no cover - prompt API differs across versions
        pass


def main() -> None:
    """Run the server over stdio."""
    if mcp is None:
        raise SystemExit(
            "the 'mcp' package is not installed; run `pip install license-sentinel` "
            "in an environment that has it"
        )
    try:
        mcp.run(transport="stdio")
    except TypeError:  # pragma: no cover - older/newer signatures
        mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
