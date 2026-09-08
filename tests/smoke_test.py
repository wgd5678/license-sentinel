"""Unit smoke tests. Run directly: python tests/smoke_test.py

No pytest, no network, no fixtures on disk - the point is that a fresh clone
can verify itself with nothing but a Python interpreter.
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from license_sentinel.collectors import collect  # noqa: E402
from license_sentinel.licenses import (  # noqa: E402
    Category,
    Context,
    Verdict,
    classify,
    evaluate,
    normalize_context,
)
from license_sentinel.report import render_notices  # noqa: E402


# --------------------------------------------------------------------------
# 1. permissive licenses are clean
# --------------------------------------------------------------------------
def test_permissive_dependencies_are_clean():
    for raw in ("MIT", "Apache-2.0", "BSD-3-Clause", "ISC", "Unlicense"):
        verdict, lic, _ = evaluate(raw, Context.PROPRIETARY)
        assert verdict is Verdict.CLEAN, f"{raw} should be CLEAN, got {verdict}"
        assert lic.category is Category.PERMISSIVE, raw


# --------------------------------------------------------------------------
# 2. strong copyleft blocks a closed-source product
# --------------------------------------------------------------------------
def test_gpl_blocks_closed_source_distribution():
    verdict, lic, reason = evaluate("GPL-3.0", Context.PROPRIETARY)
    assert verdict is Verdict.BLOCK, verdict
    assert lic.id == "GPL-3.0", lic.id
    assert "strong copyleft" in reason


# --------------------------------------------------------------------------
# 3. context decides: AGPL blocks a SaaS backend, plain GPL only needs review
# --------------------------------------------------------------------------
def test_context_changes_the_verdict():
    agpl_saas, _, _ = evaluate("AGPL-3.0", Context.SAAS_BACKEND)
    agpl_prop, _, _ = evaluate("AGPL-3.0", Context.PROPRIETARY)
    gpl_saas, _, _ = evaluate("GPL-3.0", Context.SAAS_BACKEND)
    assert agpl_saas is Verdict.BLOCK, "AGPL must block a hosted service"
    assert agpl_prop is Verdict.BLOCK, "AGPL must block a distributed product"
    assert gpl_saas is Verdict.REVIEW, "GPL on an undistributed backend is REVIEW"


# --------------------------------------------------------------------------
# 4. source-available blocks everyone; weak copyleft does not block copyleft
# --------------------------------------------------------------------------
def test_source_available_and_copyleft_ok():
    busl, _, _ = evaluate("BUSL-1.1", Context.COPYLEFT_OK)
    elastic, _, _ = evaluate("Elastic-2.0", Context.COPYLEFT_OK)
    lgpl, _, _ = evaluate("LGPL-3.0", Context.COPYLEFT_OK)
    assert busl is Verdict.BLOCK, "BUSL is not open source at all"
    assert elastic is Verdict.BLOCK, "Elastic-2.0 is not open source at all"
    assert lgpl is Verdict.CLEAN, "LGPL is fine inside a copyleft project"


# --------------------------------------------------------------------------
# 5. the spelling variants that used to slip through
# --------------------------------------------------------------------------
def test_gplv3_spelling_variants():
    variants = (
        "GPL-3.0",
        "GPLv3",
        "gpl-3",
        "GPL 3.0",
        "GNU General Public License v3 (GPLv3)",
        "GNU GENERAL PUBLIC LICENSE Version 3",
    )
    for raw in variants:
        verdict, lic, _ = evaluate(raw, Context.PROPRIETARY)
        assert lic.id == "GPL-3.0", f"{raw!r} classified as {lic.id}"
        assert verdict is Verdict.BLOCK, raw
    # the display form must stay canonical, never "GPLV3"
    assert "V" not in classify("GPLv3").id.replace("-", ""), classify("GPLv3").id


# --------------------------------------------------------------------------
# 6. multi-license expressions
# --------------------------------------------------------------------------
def test_license_expressions():
    or_verdict, or_lic, _ = evaluate("GPL-3.0 OR MIT", Context.PROPRIETARY)
    and_verdict, and_lic, _ = evaluate("MIT AND GPL-3.0", Context.PROPRIETARY)
    dual, _, _ = evaluate("MIT OR Apache-2.0", Context.PROPRIETARY)
    assert or_lic.id == "MIT", "OR lets you pick the permissive side"
    assert or_verdict is Verdict.CLEAN
    assert and_lic.id == "GPL-3.0", "AND means every term applies"
    assert and_verdict is Verdict.BLOCK
    assert dual is Verdict.CLEAN
    # "or-later" must not be mistaken for an OR expression
    assert classify("GPL-2.0-or-later").id == "GPL-2.0-or-later"


# --------------------------------------------------------------------------
# 7. classifiers and missing licenses
# --------------------------------------------------------------------------
def test_classifiers_and_unknown():
    assert classify("License :: OSI Approved :: MIT License").id == "MIT"
    assert classify("License :: OSI Approved :: GNU General Public License v3 (GPLv3)").id == "GPL-3.0"
    for missing in (None, "", "   ", "UNKNOWN"):
        verdict, lic, reason = evaluate(missing, Context.PROPRIETARY)
        assert lic.id == "UNKNOWN", missing
        assert verdict is Verdict.REVIEW, "an unlicensed dependency is all rights reserved"
        assert "all rights reserved" in reason
    try:
        normalize_context("nonsense")
    except ValueError as exc:
        assert "proprietary" in str(exc), "the error must list the valid contexts"
    else:  # pragma: no cover
        raise AssertionError("an unknown context must raise ValueError")


# --------------------------------------------------------------------------
# 8. collectors read real manifests
# --------------------------------------------------------------------------
def test_collectors_read_manifests():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "requirements.txt").write_text(
            "# comment\n-r other.txt\nflask==3.0.0  # pinned\nsome-gpl-tool>=1.2\n-e .\n",
            encoding="utf-8",
        )
        (root / "pyproject.toml").write_text(
            '[project]\nname = "demo"\ndependencies = ["requests>=2.31.0"]\n\n'
            "[dependency-groups]\ndev = [\"pytest\"]\n",
            encoding="utf-8",
        )
        (root / "package.json").write_text(
            '{"name": "demo", "dependencies": {"react": "^18.2.0"}}',
            encoding="utf-8",
        )
        node_modules = root / "node_modules" / "@scope" / "gpl-helper"
        node_modules.mkdir(parents=True)
        (node_modules / "package.json").write_text(
            '{"name": "@scope/gpl-helper", "version": "1.0.0", "license": "GPL-3.0"}',
            encoding="utf-8",
        )

        deps = collect(root)
        names = {d.name.lower() for d in deps}

        assert "flask" in names, names
        assert "requests" in names, names
        assert "pytest" in names, names
        assert "react" in names, names
        assert "@scope/gpl-helper" in names, "scoped npm packages must be found"

        by_name = {d.name.lower(): d for d in deps}
        assert by_name["flask"].version == "3.0.0"
        assert by_name["@scope/gpl-helper"].license == "GPL-3.0"
        assert by_name["react"].license is None, "declared-only deps have no license yet"

        notices = render_notices(deps, project_name="demo")
        assert "flask" in notices and "@scope/gpl-helper" in notices
        assert "Needs a license decision" in notices


# --------------------------------------------------------------------------
# 9. an empty directory must never report the server's own environment
# --------------------------------------------------------------------------
def test_empty_directory_reports_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        assert collect(tmp) == [], "an empty project has no dependencies"
    with tempfile.TemporaryDirectory() as tmp:
        # a directory that only has a .venv pointing at the running interpreter
        # must still not leak the server's own packages
        root = Path(tmp)
        (root / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
        deps = collect(root)
        assert [d for d in deps if d.name.lower() in {"mcp", "pip", "setuptools"}] == [], deps


TESTS = (
    test_permissive_dependencies_are_clean,
    test_gpl_blocks_closed_source_distribution,
    test_context_changes_the_verdict,
    test_source_available_and_copyleft_ok,
    test_gplv3_spelling_variants,
    test_license_expressions,
    test_classifiers_and_unknown,
    test_collectors_read_manifests,
    test_empty_directory_reports_nothing,
)


def main() -> int:
    failures = 0
    for test in TESTS:
        try:
            test()
        except Exception:
            failures += 1
            print(f"FAIL  {test.__name__}")
            traceback.print_exc()
        else:
            print(f"ok    {test.__name__}")
    total = len(TESTS)
    print(f"\n{total - failures}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
