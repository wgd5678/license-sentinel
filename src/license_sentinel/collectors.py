"""Dependency collection from what is actually on disk.

Reads, in this order of trust:

1. installed packages in a local virtualenv (``.venv`` / ``venv`` / ``env``) via
   ``*.dist-info/METADATA`` - real license metadata, straight from the wheel
2. ``requirements.txt``
3. ``pyproject.toml`` (PEP 621, poetry, PEP 735 dependency-groups)
4. ``node_modules/*/package.json`` (including scoped packages)
5. ``package.json`` dependency declarations

Declared-but-not-installed dependencies are kept with an ``UNKNOWN`` license
instead of being dropped: an unlicensed dependency is all-rights-reserved by
default, and silently hiding it is the most expensive kind of bug.

No HTTP client is imported anywhere in this package. This is a read-only scan
and nothing leaves the machine.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - requires-python is >=3.11
    tomllib = None  # type: ignore[assignment]

__all__ = ["Dependency", "collect", "normalize_name", "SCAN_CURRENT_ENV_FLAG"]

SCAN_CURRENT_ENV_FLAG = "LICENSE_SENTINEL_SCAN_CURRENT_ENV"

_VENV_DIRS = (".venv", "venv", "env", "virtualenv")
_SITE_PACKAGES_GLOBS = (
    "*/site-packages",
    "Lib/site-packages",
    "lib/site-packages",
    "*/*/site-packages",
    "*/*/*/site-packages",
)

# requirements.txt: name, optional extras, rest (version spec + markers)
_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")
_REQ_STRIP_COMMENT = re.compile(r"\s+#.*$")


@dataclass(frozen=True)
class Dependency:
    """One third-party package found in a project."""

    name: str
    version: str | None = None
    license: str | None = None
    ecosystem: str = "python"
    source: str = "declared"
    installed: bool = False

    @property
    def label(self) -> str:
        return f"{self.name}=={self.version}" if self.version else self.name

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.label


def normalize_name(name: str) -> str:
    """PEP 503-ish normalization so ``foo_bar`` and ``foo-bar`` merge."""
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


# --------------------------------------------------------------------------
# guard: never scan the environment the server itself is running in
# --------------------------------------------------------------------------


def _current_env_paths() -> set[Path]:
    paths: set[Path] = set()
    for attr in ("prefix", "base_prefix", "exec_prefix", "base_exec_prefix"):
        value = getattr(sys, attr, None)
        if value:
            try:
                paths.add(Path(value).resolve())
            except OSError:  # pragma: no cover - defensive
                pass
    try:
        import sysconfig

        for key in ("purelib", "platlib", "stdlib", "data"):
            value = sysconfig.get_paths().get(key)
            if value:
                paths.add(Path(value).resolve())
    except Exception:  # pragma: no cover - defensive
        pass
    return paths


def _scan_current_env_allowed() -> bool:
    return os.environ.get(SCAN_CURRENT_ENV_FLAG, "").strip() in {"1", "true", "yes", "on"}


def _is_current_env(candidate: Path, env_paths: set[Path]) -> bool:
    try:
        resolved = candidate.resolve()
    except OSError:  # pragma: no cover - broken symlink
        return True
    return any(resolved == env or env in resolved.parents for env in env_paths)


# --------------------------------------------------------------------------
# python
# --------------------------------------------------------------------------


def _parse_metadata(text: str) -> dict[str, list[str]]:
    """Minimal RFC822 header parse of dist-info METADATA (headers only)."""
    fields: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line.strip():
            if fields:
                break  # end of headers, the description follows
            continue
        if line[:1] in (" ", "\t"):
            continue  # folded continuation - not needed for our fields
        key, sep, value = line.partition(":")
        if not sep:
            continue
        fields.setdefault(key.strip().lower(), []).append(value.strip())
    return fields


def _license_from_metadata(fields: dict[str, list[str]]) -> str | None:
    # PEP 639: a machine-readable SPDX expression wins when present.
    for key in ("license-expression", "license_expression"):
        if fields.get(key):
            return fields[key][0]

    license_text = fields.get("license", [None])[0]
    if license_text and len(license_text) < 200 and "\n" not in license_text:
        # Short inline value (modern metadata). Long blocks are license bodies.
        return license_text

    for classifier in fields.get("classifier", []):
        if classifier.lower().startswith("license ::"):
            return classifier
    return None


def _find_venvs(root: Path) -> list[Path]:
    return [root / name for name in _VENV_DIRS if (root / name).is_dir()]


def _site_packages_dirs(venv: Path) -> list[Path]:
    found: dict[Path, None] = {}
    for pattern in _SITE_PACKAGES_GLOBS:
        for match in venv.glob(pattern):
            if match.is_dir():
                found.setdefault(match, None)
    return list(found)


def _collect_installed_python(root: Path, env_paths: set[Path]) -> list[Dependency]:
    deps: list[Dependency] = []
    for venv in _find_venvs(root):
        for site_packages in _site_packages_dirs(venv):
            if not _scan_current_env_allowed() and _is_current_env(site_packages, env_paths):
                continue  # this is the server's own environment, not the project's
            for metadata in sorted(site_packages.glob("*.dist-info/METADATA")):
                fields = _parse_metadata(metadata.read_text(encoding="utf-8", errors="replace"))
                name = fields.get("name", [None])[0]
                if not name:
                    continue
                deps.append(
                    Dependency(
                        name=name,
                        version=fields.get("version", [None])[0],
                        license=_license_from_metadata(fields),
                        ecosystem="python",
                        source="installed",
                        installed=True,
                    )
                )
    return deps


def _parse_requirement_line(line: str) -> tuple[str, str | None] | None:
    line = _REQ_STRIP_COMMENT.sub("", line).strip()
    if not line or line.startswith(("-", "#")):
        return None
    if "@" in line or "://" in line:
        return None  # direct reference / URL requirement
    match = _REQ_LINE.match(line)
    if not match:
        return None
    name = match.group(1)
    rest = (match.group(3) or "").strip()
    version: str | None = None
    spec = re.match(r"(===|==|!=|>=|<=|~=|==|>|<)?\s*([0-9][^\s;,]*)", rest)
    if spec and spec.group(1) and spec.group(2):
        version = spec.group(2).rstrip(",")
    return name, version


def _collect_requirements(root: Path) -> list[Dependency]:
    deps: list[Dependency] = []
    for path in sorted(root.glob("requirements*.txt")):
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if raw.lstrip().startswith(("-r", "--requirement", "-c", "--constraint")):
                continue
            parsed = _parse_requirement_line(raw)
            if not parsed:
                continue
            name, version = parsed
            deps.append(
                Dependency(
                    name=name,
                    version=version,
                    ecosystem="python",
                    source=path.name,
                )
            )
    return deps


def _collect_pyproject(root: Path) -> list[Dependency]:
    path = root / "pyproject.toml"
    if not path.is_file() or tomllib is None:
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []  # a broken pyproject must not fail the whole audit

    deps: list[Dependency] = []
    project = data.get("project", {}) or {}

    def add_requirements(items: object, source: str) -> None:
        if isinstance(items, list):
            for item in items:
                if isinstance(item, str):
                    parsed = _parse_requirement_line(item)
                    if parsed:
                        deps.append(
                            Dependency(
                                name=parsed[0],
                                version=parsed[1],
                                ecosystem="python",
                                source=source,
                            )
                        )

    add_requirements(project.get("dependencies"), "pyproject.toml")

    for group in (project.get("optional-dependencies") or {}).values():
        add_requirements(group, "pyproject.toml")

    # PEP 735 dependency-groups
    for group in (data.get("dependency-groups") or {}).values():
        add_requirements(group, "pyproject.toml")

    # poetry: [tool.poetry.dependencies] is a mapping of name -> spec
    poetry = (data.get("tool") or {}).get("poetry") or {}
    poetry_deps = poetry.get("dependencies") or {}
    if isinstance(poetry_deps, dict):
        for name, spec in poetry_deps.items():
            if not isinstance(name, str) or name.lower() == "python":
                continue
            version = spec if isinstance(spec, str) else None
            if isinstance(spec, dict):
                version = spec.get("version")
            deps.append(
                Dependency(name=name, version=version, ecosystem="python", source="pyproject.toml")
            )

    return deps


# --------------------------------------------------------------------------
# npm
# --------------------------------------------------------------------------


def _license_from_package_json(data: dict) -> str | None:
    lic = data.get("license")
    if isinstance(lic, str):
        return lic
    if isinstance(lic, dict):
        value = lic.get("type") or lic.get("name")
        return value if isinstance(value, str) else None
    legacy = data.get("licenses")
    if isinstance(legacy, list) and legacy:
        first = legacy[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            value = first.get("type") or first.get("name")
            return value if isinstance(value, str) else None
    return None


def _read_package_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _collect_node_modules(root: Path) -> list[Dependency]:
    node_modules = root / "node_modules"
    if not node_modules.is_dir():
        return []

    deps: list[Dependency] = []
    patterns = (
        "*/package.json",  # flat, npm v3+
        "@*/*/package.json",  # scoped packages
        "*/node_modules/*/package.json",  # nested
        "*/node_modules/@*/*/package.json",
    )
    seen: set[Path] = set()
    for pattern in patterns:
        for candidate in node_modules.glob(pattern):
            if candidate in seen:
                continue
            seen.add(candidate)
            data = _read_package_json(candidate)
            if not data:
                continue
            name = data.get("name")
            if not isinstance(name, str) or not name:
                continue
            version = data.get("version")
            deps.append(
                Dependency(
                    name=name,
                    version=version if isinstance(version, str) else None,
                    license=_license_from_package_json(data),
                    ecosystem="npm",
                    source="installed",
                    installed=True,
                )
            )
    return deps


def _collect_package_json(root: Path) -> list[Dependency]:
    data = _read_package_json(root / "package.json")
    if not data:
        return []
    deps: list[Dependency] = []
    for field in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        block = data.get(field)
        if not isinstance(block, dict):
            continue
        for name, spec in block.items():
            if not isinstance(name, str):
                continue
            deps.append(
                Dependency(
                    name=name,
                    version=spec.lstrip("^~>=<") if isinstance(spec, str) else None,
                    ecosystem="npm",
                    source="package.json",
                )
            )
    return deps


# --------------------------------------------------------------------------
# merge + entry point
# --------------------------------------------------------------------------


def _merge(deps: list[Dependency]) -> list[Dependency]:
    """Collapse the same package from several sources, keeping the best data."""
    merged: dict[tuple[str, str], Dependency] = {}
    for dep in deps:
        key = (dep.ecosystem, normalize_name(dep.name))
        existing = merged.get(key)
        if existing is None:
            merged[key] = dep
            continue
        merged[key] = Dependency(
            name=dep.name,
            version=dep.version or existing.version,
            license=dep.license or existing.license,
            ecosystem=dep.ecosystem,
            source=dep.source if dep.installed else existing.source,
            installed=dep.installed or existing.installed,
        )
    return sorted(merged.values(), key=lambda d: (d.ecosystem, normalize_name(d.name)))


def collect(path: str | Path = ".") -> list[Dependency]:
    """Collect every dependency of the project at ``path``.

    Nothing here falls back to the interpreter's own site-packages: an empty
    directory has no dependencies, and reporting the server's 39 packages as
    yours is worse than reporting nothing.
    """
    root = Path(path).expanduser()
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is not a directory")

    env_paths = _current_env_paths()
    deps: list[Dependency] = []
    deps += _collect_installed_python(root, env_paths)
    deps += _collect_requirements(root)
    deps += _collect_pyproject(root)
    deps += _collect_node_modules(root)
    deps += _collect_package_json(root)
    return _merge(deps)
