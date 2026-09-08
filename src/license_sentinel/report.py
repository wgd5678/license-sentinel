"""Audit orchestration and output rendering.

Turns a list of :class:`~license_sentinel.collectors.Dependency` into verdicts
(``CLEAN`` / ``REVIEW`` / ``BLOCK``) and renders them either as a terminal
report or as a ``THIRD-PARTY-NOTICES.md`` attribution document.

Not legal advice. It is a fast first pass that catches the expensive mistakes;
have counsel review anything flagged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .collectors import Dependency, collect
from .licenses import Context, Finding, Verdict, evaluate, normalize_context

__all__ = ["AuditResult", "audit", "render_audit", "render_notices", "write_notices"]

_DEFAULT_NOTICES = "THIRD-PARTY-NOTICES.md"

_CONTEXT_LABELS = {
    Context.PROPRIETARY: "closed-source product you distribute",
    Context.SAAS_BACKEND: "service you run, never distributed",
    Context.PERMISSIVE: "your own project licensed MIT/Apache/BSD",
    Context.COPYLEFT_OK: "your own project licensed under the GPL family",
}


@dataclass
class AuditResult:
    """Everything the audit found, ready to render."""

    path: str
    context: Context
    dependencies: list[Dependency] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.verdict is Verdict.BLOCK]

    @property
    def needs_review(self) -> list[Finding]:
        return [f for f in self.findings if f.verdict is Verdict.REVIEW]

    @property
    def clean(self) -> list[Finding]:
        return [f for f in self.findings if f.verdict is Verdict.CLEAN]

    @property
    def counts(self) -> dict[str, int]:
        return {
            "total": len(self.findings),
            "BLOCK": len(self.blocking),
            "REVIEW": len(self.needs_review),
            "CLEAN": len(self.clean),
        }

    @property
    def ok(self) -> bool:
        return not self.blocking


def audit(
    path: str | Path = ".",
    context: Context | str = Context.PROPRIETARY,
) -> AuditResult:
    """Scan ``path`` and judge every dependency against ``context``."""
    ctx = normalize_context(context)
    root = Path(path).expanduser()
    dependencies = collect(root)
    findings = [
        Finding(
            name=dep.name,
            license=license_obj,
            verdict=verdict,
            reason=reason,
            context=ctx,
            version=dep.version,
            ecosystem=dep.ecosystem,
        )
        for dep in dependencies
        for verdict, license_obj, reason in (evaluate(dep.license, ctx),)
    ]
    findings.sort(key=lambda f: (f.verdict is not Verdict.BLOCK, f.verdict is not Verdict.REVIEW, f.name.lower()))
    return AuditResult(path=str(root), context=ctx, dependencies=dependencies, findings=findings)


def render_audit(result: AuditResult) -> str:
    """Render an audit as plain text (ASCII only, safe on any terminal)."""
    counts = result.counts
    lines: list[str] = [
        f"license-sentinel audit: {result.path}",
        f"context: {result.context.value} ({_CONTEXT_LABELS[result.context]})",
        "",
    ]

    if not result.findings:
        lines.append("No dependencies found. Nothing to audit.")
        lines.append("")
        lines.append(
            "If you expected dependencies here, check that the path contains a "
            "requirements.txt, pyproject.toml, package.json or a local .venv."
        )
        return "\n".join(lines)

    lines.append(
        f"{counts['total']} dependencies: "
        f"{counts['BLOCK']} BLOCK, {counts['REVIEW']} REVIEW, {counts['CLEAN']} CLEAN"
    )
    lines.append("")

    if result.blocking:
        lines.append("BLOCK - do not ship until these are resolved")
        for finding in result.blocking:
            label = f"{finding.name}=={finding.version}" if finding.version else finding.name
            lines.append(f"  [X] {label}  ({finding.ecosystem}, {finding.license.id})")
            lines.append(f"      {finding.reason}")
        lines.append("")

    if result.needs_review:
        lines.append("REVIEW - probably fine, but a human has to confirm")
        for finding in result.needs_review:
            label = f"{finding.name}=={finding.version}" if finding.version else finding.name
            raw = finding.license.raw or "not declared"
            lines.append(f"  [!] {label}  ({finding.ecosystem}, {finding.license.id} <- {raw!r})")
            lines.append(f"      {finding.reason}")
        lines.append("")

    if result.clean:
        names = ", ".join(f.name for f in result.clean[:12])
        more = "" if len(result.clean) <= 12 else f" and {len(result.clean) - 12} more"
        lines.append(f"CLEAN ({len(result.clean)}): {names}{more}")
        lines.append("")

    if result.blocking:
        lines.append("Verdict: BLOCKED. One or more dependencies cannot ship in this context.")
    elif result.needs_review:
        lines.append("Verdict: REVIEW. No hard blockers, but confirm the items above.")
    else:
        lines.append("Verdict: CLEAN. Nothing in the scanned dependencies blocks this context.")
    lines.append("Not legal advice - confirm anything flagged before you ship.")
    return "\n".join(lines)


def render_notices(
    dependencies: list[Dependency],
    project_name: str | None = None,
) -> str:
    """Render a THIRD-PARTY-NOTICES.md attribution document."""
    title = project_name or "this project"
    grouped: dict[str, list[Dependency]] = {}
    for dep in dependencies:
        grouped.setdefault(dep.ecosystem, []).append(dep)

    lines: list[str] = [
        "# Third-party notices",
        "",
        f"{title} bundles third-party software. The lists below were generated by",
        "license-sentinel from what was installed on disk at the time of the scan.",
        "",
        "This is an attribution record, not legal advice. Packages marked UNKNOWN",
        "declare no license: they are all rights reserved until the author says",
        "otherwise, and they need a human decision before release.",
        "",
    ]

    for ecosystem in ("python", "npm"):
        deps = sorted(grouped.get(ecosystem, []), key=lambda d: d.name.lower())
        if not deps:
            continue
        heading = "Python dependencies" if ecosystem == "python" else "npm dependencies"
        lines += [f"## {heading}", "", "| Package | Version | License |", "|---|---|---|"]
        for dep in deps:
            version = dep.version or "-"
            lic = dep.license or "UNKNOWN"
            lines.append(f"| {dep.name} | {version} | {lic} |")
        lines.append("")

    unknown = [d for d in dependencies if not d.license]
    if unknown:
        lines += [
            "## Needs a license decision",
            "",
            "These dependencies declare no license and cannot be attributed yet:",
            "",
        ]
        lines += [f"- {d.name} ({d.ecosystem})" for d in unknown]
        lines.append("")

    if not dependencies:
        lines.append("No third-party dependencies were found in the scanned project.")
        lines.append("")

    return "\n".join(lines)


def write_notices(
    path: str | Path = ".",
    output: str | Path = _DEFAULT_NOTICES,
    project_name: str | None = None,
) -> tuple[str, int]:
    """Write the notices file and return ``(written_path, dependency_count)``."""
    root = Path(path).expanduser()
    dependencies = collect(root)
    if project_name is None:
        project_name = root.resolve().name

    target = Path(output)
    if not target.is_absolute():
        target = root / target
    if target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)

    target.write_text(render_notices(dependencies, project_name), encoding="utf-8")
    return str(target), len(dependencies)
