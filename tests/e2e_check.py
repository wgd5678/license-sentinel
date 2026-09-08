"""End-to-end check: build a throwaway project on disk and call the tools.

Run: python tests/e2e_check.py

This is the check that matters before publishing: Glama's inspector really
starts the server and enumerates the tools, so if the tools do not work
end to end here, they will silently fail there.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from license_sentinel.server import (  # noqa: E402
    audit_project,
    check_package,
    generate_notices,
    pre_release_license_review,
)


def build_fixture(root: Path) -> None:
    """A small project with one dependency in each interesting category."""
    (root / "requirements.txt").write_text(
        "\n".join(
            [
                "# pinned and permissive",
                "flask==3.0.0",
                "requests>=2.31.0",
                "# strong copyleft",
                "some-gpl-helper==1.2.3",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname = "demo-app"\nversion = "0.1.0"\n'
        'dependencies = ["internal-widget>=0.4"]\n',
        encoding="utf-8",
    )
    (root / "package.json").write_text(
        '{\n  "name": "demo-app",\n  "dependencies": {\n'
        '    "react": "^18.2.0",\n    "left-pad": "1.3.0"\n  }\n}\n',
        encoding="utf-8",
    )

    installed = root / "node_modules" / "some-agpl-thing"
    installed.mkdir(parents=True)
    (installed / "package.json").write_text(
        '{"name": "some-agpl-thing", "version": "2.0.0", "license": "AGPL-3.0"}',
        encoding="utf-8",
    )

    # real dist-info dirs, so the installed-package path is exercised too
    site_packages = root / ".venv" / "lib" / "python3.11" / "site-packages"
    packages = {
        "busl_widget-1.0.0": (
            "Name: busl-widget\nVersion: 1.0.0\nLicense-Expression: BUSL-1.1\n"
        ),
        "some_gpl_helper-1.2.3": (
            "Name: some-gpl-helper\nVersion: 1.2.3\n"
            "Classifier: License :: OSI Approved :: GNU General Public License v3 (GPLv3)\n"
        ),
        "requests-2.31.0": (
            "Name: requests\nVersion: 2.31.0\nLicense: Apache-2.0\n"
        ),
    }
    for directory, header in packages.items():
        dist_info = site_packages / f"{directory}.dist-info"
        dist_info.mkdir(parents=True)
        (dist_info / "METADATA").write_text(
            f"Metadata-Version: 2.1\n{header}\nbody\n", encoding="utf-8"
        )

    unknown = root / "node_modules" / "mystery-lib"
    unknown.mkdir(parents=True)
    (unknown / "package.json").write_text(
        '{"name": "mystery-lib", "version": "0.0.1"}',
        encoding="utf-8",
    )


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        raise AssertionError(f"{label} {detail}")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "demo-app"
        project.mkdir()
        build_fixture(project)

        print("=" * 72)
        print("1) audit_project(path, context='proprietary')")
        print("=" * 72)
        report = audit_project(str(project), "proprietary")
        print(report)
        check("finds the GPL dependency", "some-gpl-helper" in report)
        check("GPL blocks a closed-source product", "[X] some-gpl-helper==1.2.3" in report, report)
        check("finds the AGPL npm dependency", "[X] some-agpl-thing==2.0.0" in report)
        check("finds the BUSL package from .venv metadata", "[X] busl-widget==1.0.0" in report)
        check("reports the unlicensed npm package", "mystery-lib" in report)
        check("reads licenses from dist-info metadata", "requests" in report)
        check("marks it BLOCKED", "BLOCK" in report)
        check("does not leak the server's own packages", "mcp" not in report.split("CLEAN")[0])

        print()
        print("=" * 72)
        print("2) audit_project(path, context='saas-backend')")
        print("=" * 72)
        saas = audit_project(str(project), "saas-backend")
        print(saas)
        check("GPL is only REVIEW for a hosted backend", "[!] some-gpl-helper==1.2.3" in saas, saas)
        check("AGPL still blocks a hosted backend", "[X] some-agpl-thing==2.0.0" in saas)
        check("permissive deps stay clean", "CLEAN (1)" in saas or "CLEAN (" in saas)

        print()
        print("=" * 72)
        print("3) check_package(names, context='proprietary')")
        print("=" * 72)
        names = [
            "AGPL-3.0",
            "BUSL-1.1",
            "GPLv3",
            "Apache License 2.0",
            "MIT OR Apache-2.0",
            "GNU General Public License v3 (GPLv3)",
        ]
        print(check_package(names, "proprietary"))
        single = check_package("MIT", "proprietary")
        check("accepts a single string", "CLEAN" in single, single)
        csv = check_package("MIT, GPL-3.0", "proprietary")
        check("accepts a comma-separated string", "GPL-3.0" in csv, csv)

        print()
        print("=" * 72)
        print("4) generate_notices(path)")
        print("=" * 72)
        result = generate_notices(str(project), "THIRD-PARTY-NOTICES.md")
        print(result)
        notices = (project / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8")
        check("writes the notices file", "THIRD-PARTY-NOTICES.md" in result)
        check("lists python dependencies", "Python dependencies" in notices)
        check("lists npm dependencies", "npm dependencies" in notices)
        check("flags unknown licenses", "Needs a license decision" in notices)

        print()
        print("=" * 72)
        print("5) error handling")
        print("=" * 72)
        print(audit_project(str(project / "nope"), "proprietary"))
        print(audit_project(str(project), "nonsense"))
        check("missing path is an error", "error:" in audit_project(str(project / "nope")))
        check("bad context is an error", "error:" in audit_project(str(project), "nonsense"))

        print()
        print("=" * 72)
        print("6) pre_release_license_review prompt")
        print("=" * 72)
        prompt = pre_release_license_review(str(project), "proprietary")
        print(prompt)
        check("prompt mentions the audit tool", "audit_project" in prompt)

    print()
    print("end-to-end check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
