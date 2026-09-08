"""SPDX license classification and distribution-context verdicts.

Pure logic: no I/O, no network, no third-party imports. Everything here is
deterministic, which is what makes it testable and safe to run offline.

Two jobs:

1. ``classify()`` - turn the messy strings package managers emit ("Apache License
   2.0", "GPLv3", "GNU General Public License v3 (GPLv3)", "License :: OSI
   Approved :: MIT License") into a canonical id plus a category.
2. ``evaluate()`` - judge that license against *how you distribute your product*.
   The same dependency is clean in one context and fatal in another, so the
   context is a required input, not an afterthought.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "CONTEXTS",
    "Category",
    "Context",
    "Finding",
    "License",
    "Verdict",
    "classify",
    "evaluate",
    "normalize_context",
    "unknown_license",
]


class Category(str, Enum):
    """How a license behaves once you ship something that contains it."""

    PERMISSIVE = "permissive"
    WEAK_COPYLEFT = "weak-copyleft"
    STRONG_COPYLEFT = "strong-copyleft"
    NETWORK_COPYLEFT = "network-copyleft"
    SOURCE_AVAILABLE = "source-available"
    NON_COMMERCIAL = "non-commercial"
    PROPRIETARY = "proprietary"
    UNKNOWN = "unknown"


class Verdict(str, Enum):
    CLEAN = "CLEAN"
    REVIEW = "REVIEW"
    BLOCK = "BLOCK"


class Context(str, Enum):
    """How the *user's* product is distributed."""

    PROPRIETARY = "proprietary"
    SAAS_BACKEND = "saas-backend"
    PERMISSIVE = "permissive"
    COPYLEFT_OK = "copyleft-ok"


CONTEXTS: tuple[str, ...] = tuple(c.value for c in Context)


# --------------------------------------------------------------------------
# normalization
# --------------------------------------------------------------------------

# Keep "+" (GPL-2.0+) and "." (GPL-3.0); everything else becomes a separator.
# This is what makes "GPL-3.0", "GPLv3" and "GPL 3" collapse to the same shape,
# and it is why "GNU General Public License v3 (GPLv3)" is no longer missed.
_NON_WORD = re.compile(r"[^a-z0-9+.]+")
_WHITESPACE = re.compile(r"\s+")

# Trove classifiers such as "License :: OSI Approved :: MIT License".
_CLASSIFIER_PREFIX = re.compile(r"^\s*(?:license\s*::\s*)+(?:osi\s+approved\s*::\s*)?", re.IGNORECASE)
_CLASSIFIER_SUFFIX = re.compile(r"\s+license\s*$", re.IGNORECASE)


def _normalize(raw: str) -> str:
    """Lowercase and collapse punctuation so patterns can rely on word breaks."""
    if not raw:
        return ""
    text = raw.strip()
    text = _CLASSIFIER_PREFIX.sub("", text)
    text = _CLASSIFIER_SUFFIX.sub("", text)
    text = _NON_WORD.sub(" ", text.lower())
    return _WHITESPACE.sub(" ", text).strip()


@dataclass(frozen=True)
class License:
    """A classified license string."""

    id: str
    category: Category
    raw: str = ""
    expression: bool = False

    @property
    def is_unknown(self) -> bool:
        return self.category is Category.UNKNOWN

    def __str__(self) -> str:
        return self.id


def unknown_license(raw: str = "") -> License:
    """A dependency with no license is all-rights-reserved by default."""
    return License(id="UNKNOWN", category=Category.UNKNOWN, raw=raw)


# --------------------------------------------------------------------------
# pattern table
# --------------------------------------------------------------------------

# Order matters: the most specific and most dangerous families come first, so
# "GNU Affero General Public License" is never mistaken for plain GPL.
_PATTERNS: tuple[tuple[re.Pattern[str], str, Category], ...] = (
    # network copyleft: triggered by serving, not by distributing
    (re.compile(r"\b(?:sspl|server\s+side\s+public\s+license)\b"), "SSPL-1.0", Category.NETWORK_COPYLEFT),
    (re.compile(r"\b(?:agpl|affero)"), "AGPL-3.0", Category.NETWORK_COPYLEFT),
    # source-available: not open source at all
    (re.compile(r"\b(?:busl|business\s+source\s+license)\b"), "BUSL-1.1", Category.SOURCE_AVAILABLE),
    (re.compile(r"\b(?:elastic\s+license|elastic\s*2|elv2)\b"), "Elastic-2.0", Category.SOURCE_AVAILABLE),
    (re.compile(r"\bcommons\s+clause\b"), "Commons-Clause", Category.SOURCE_AVAILABLE),
    (re.compile(r"\b(?:fair\s+source|fsl)\b"), "FSL-1.1", Category.SOURCE_AVAILABLE),
    # non-commercial
    (re.compile(r"\bnon[\s-]*commercial\b"), "Non-Commercial", Category.NON_COMMERCIAL),
    (re.compile(r"\bcc[\s-]*by[\s-]*nc\b"), "CC-BY-NC-4.0", Category.NON_COMMERCIAL),
    # strong copyleft
    (
        re.compile(r"\b(?:gpl|gnu\s+general\s+public\s+license)\s*v?\.?\s*([23](?:\.\d)?)"),
        "GPL-{v}",
        Category.STRONG_COPYLEFT,
    ),
    (re.compile(r"\b(?:gpl|gnu\s+general\s+public\s+license)\b"), "GPL-3.0", Category.STRONG_COPYLEFT),
    (re.compile(r"\beupl\b"), "EUPL-1.2", Category.STRONG_COPYLEFT),
    (re.compile(r"\bcc[\s-]*by[\s-]*sa\b"), "CC-BY-SA-4.0", Category.STRONG_COPYLEFT),
    # weak copyleft
    (
        re.compile(r"\b(?:lgpl|gnu\s+lesser|gnu\s+library)\s*v?\.?\s*([23](?:\.\d)?)"),
        "LGPL-{v}",
        Category.WEAK_COPYLEFT,
    ),
    (re.compile(r"\b(?:lgpl|gnu\s+lesser|gnu\s+library)\b"), "LGPL-3.0", Category.WEAK_COPYLEFT),
    (
        re.compile(r"\b(?:mpl|mozilla\s+public\s+license)\s*v?\.?\s*([12](?:\.\d)?)"),
        "MPL-{v}",
        Category.WEAK_COPYLEFT,
    ),
    (re.compile(r"\b(?:mpl|mozilla\s+public\s+license)\b"), "MPL-2.0", Category.WEAK_COPYLEFT),
    (re.compile(r"\b(?:epl|eclipse\s+public\s+license)"), "EPL-2.0", Category.WEAK_COPYLEFT),
    (re.compile(r"\b(?:cddl|common\s+development)"), "CDDL-1.1", Category.WEAK_COPYLEFT),
    (re.compile(r"\b(?:ms[\s-]*rl|microsoft\s+reciprocal)"), "MS-RL", Category.WEAK_COPYLEFT),
    # proprietary / custom / unreadable
    (
        re.compile(
            r"\b(?:proprietary|commercial\s+license|see\s+license|custom\s+license"
            r"|all\s+rights\s+reserved|unlicensed)\b"
        ),
        "Proprietary",
        Category.PROPRIETARY,
    ),
    # permissive
    (re.compile(r"\bmit[\s-]*0\b"), "MIT-0", Category.PERMISSIVE),
    (re.compile(r"\bapache"), "Apache-2.0", Category.PERMISSIVE),
    (re.compile(r"\b(?:mit|expat)\b"), "MIT", Category.PERMISSIVE),
    (re.compile(r"\bb(?:sd)?[\s-]*(?:2|3|4)"), "BSD-3-Clause", Category.PERMISSIVE),
    (re.compile(r"\bbsd\b"), "BSD-3-Clause", Category.PERMISSIVE),
    (re.compile(r"\bisc\b"), "ISC", Category.PERMISSIVE),
    (re.compile(r"\bunlicense\b"), "Unlicense", Category.PERMISSIVE),
    (re.compile(r"\bcc0\b"), "CC0-1.0", Category.PERMISSIVE),
    (re.compile(r"\b0bsd\b"), "0BSD", Category.PERMISSIVE),
    (re.compile(r"\b(?:zlib|libpng)\b"), "Zlib", Category.PERMISSIVE),
    (re.compile(r"\b(?:psf|python\s+software\s+foundation)\b"), "PSF-2.0", Category.PERMISSIVE),
    (re.compile(r"\bboost\b"), "BSL-1.0", Category.PERMISSIVE),
    (re.compile(r"\bwtfpl\b"), "WTFPL", Category.PERMISSIVE),
    (re.compile(r"\bartistic\b"), "Artistic-2.0", Category.PERMISSIVE),
)


def _fmt_version(capture: str) -> str:
    """"3" -> "3.0", "2" -> "2.0", "2.1" stays "2.1"."""
    return capture if "." in capture else f"{capture}.0"


def _classify_single(raw: str) -> License:
    """Classify one license token (no OR/AND handling)."""
    text = _normalize(raw)
    if not text:
        return unknown_license(raw)

    for pattern, template, category in _PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        if "{v}" in template and match.groups() and match.group(1):
            license_id = template.format(v=_fmt_version(match.group(1)))
        else:
            license_id = template
        if license_id.startswith(("GPL-", "LGPL-", "AGPL-", "MPL-", "EPL-", "EUPL-")):
            if "+" in raw or re.search(r"\bor[\s-]*later\b", text):
                license_id = f"{license_id}-or-later"
            elif re.search(r"\b(?:only|solely)\b", text):
                license_id = f"{license_id}-only"
        return License(id=license_id, category=category, raw=raw)

    return License(id="UNKNOWN", category=Category.UNKNOWN, raw=raw)


# --------------------------------------------------------------------------
# composite expressions: "MIT OR Apache-2.0", "GPL-2.0 AND LGPL-3.0"
# --------------------------------------------------------------------------

_OR_SPLIT = re.compile(r"\s+or\s+|\s*\|\|\s*|\s*/\s*", re.IGNORECASE)
_AND_SPLIT = re.compile(r"\s+and\s+|\s*&&\s*", re.IGNORECASE)
_OR_LATER = re.compile(r"\b(?:or|and)[\s-]*later\b", re.IGNORECASE)

# How bad a category is, used to pick a representative branch out of an
# expression. Higher means stricter.
_SEVERITY: dict[Category, int] = {
    Category.PERMISSIVE: 0,
    Category.WEAK_COPYLEFT: 1,
    Category.STRONG_COPYLEFT: 2,
    Category.NETWORK_COPYLEFT: 3,
    Category.SOURCE_AVAILABLE: 4,
    Category.NON_COMMERCIAL: 5,
    Category.PROPRIETARY: 6,
    Category.UNKNOWN: 7,
}


def _has_expression(text: str) -> bool:
    """True if the string is a real OR/AND expression, ignoring "or-later"."""
    probe = _OR_LATER.sub("", text)
    return bool(_OR_SPLIT.search(probe) or _AND_SPLIT.search(probe))


def classify(raw: str | None) -> License:
    """Classify a license string, resolving OR/AND expressions.

    ``OR`` means you may pick, so the most permissive branch decides.
    ``AND`` means every term applies, so the strictest branch decides.
    """
    if raw is None or not raw.strip():
        return unknown_license(raw or "")
    if raw.strip().upper() in {"UNKNOWN", "NONE", "N/A", "NULL", "-", "?"}:
        return unknown_license(raw)
    if not _has_expression(raw):
        return _classify_single(raw)

    candidates: list[License] = []
    for or_branch in _OR_SPLIT.split(raw):
        if not or_branch.strip():
            continue
        branches = [_classify_single(part) for part in _AND_SPLIT.split(or_branch) if part.strip()]
        if branches:
            # inside one OR branch, AND means "all of them" -> strictest wins
            candidates.append(max(branches, key=lambda lic: _SEVERITY[lic.category]))

    if not candidates:
        return unknown_license(raw)

    # across OR branches you may choose -> most permissive wins
    best = min(candidates, key=lambda lic: _SEVERITY[lic.category])
    return License(id=best.id, category=best.category, raw=raw, expression=True)


# --------------------------------------------------------------------------
# context rules
# --------------------------------------------------------------------------

_REASONS: dict[Context, dict[Category, str]] = {
    Context.PROPRIETARY: {
        Category.NETWORK_COPYLEFT: (
            "copyleft that is triggered by network interaction: shipping a "
            "closed-source product containing it obliges you to release the whole "
            "work under the same license"
        ),
        Category.STRONG_COPYLEFT: (
            "strong copyleft: distributing a closed-source product that contains it "
            "requires releasing your own source under the same license"
        ),
        Category.SOURCE_AVAILABLE: (
            "source-available, not open source: commercial use beyond the granted "
            "limits requires buying a license from the vendor"
        ),
        Category.NON_COMMERCIAL: "non-commercial terms forbid shipping it inside a commercial product",
        Category.WEAK_COPYLEFT: (
            "weak copyleft: dynamic linking is usually acceptable, but static linking "
            "or patching the library triggers source-disclosure obligations"
        ),
        Category.PROPRIETARY: "custom or commercial license: the actual terms have to be read by a human",
        Category.UNKNOWN: "no license declared: treat it as all rights reserved until confirmed",
    },
    Context.SAAS_BACKEND: {
        Category.NETWORK_COPYLEFT: (
            "network copyleft: offering it to users over a network obliges you to "
            "publish your server-side source"
        ),
        Category.SOURCE_AVAILABLE: (
            "source-available: production use above the granted limits requires a "
            "commercial license even when you never distribute anything"
        ),
        Category.NON_COMMERCIAL: "non-commercial terms forbid commercial use of any kind, hosted or not",
        Category.STRONG_COPYLEFT: (
            "GPL is triggered by distribution and you do not distribute, but combining "
            "it with a proprietary backend is legally contested"
        ),
        Category.WEAK_COPYLEFT: (
            "weak copyleft: fine for an undistributed backend as long as you do not "
            "patch the library itself"
        ),
        Category.PROPRIETARY: "custom or commercial license: check whether it permits hosted use",
        Category.UNKNOWN: "no license declared: treat it as all rights reserved until confirmed",
    },
    Context.PERMISSIVE: {
        Category.NETWORK_COPYLEFT: (
            "network copyleft: it drags your permissive project into copyleft terms as "
            "soon as the result is offered over a network"
        ),
        Category.STRONG_COPYLEFT: (
            "strong copyleft: it would contaminate the permissive terms you publish "
            "your own project under"
        ),
        Category.SOURCE_AVAILABLE: (
            "source-available: not open source, and incompatible with permissive "
            "redistribution"
        ),
        Category.NON_COMMERCIAL: (
            "non-commercial terms are incompatible with a permissive open-source "
            "distribution"
        ),
        Category.WEAK_COPYLEFT: (
            "weak copyleft: acceptable when dynamically linked, but it can impose "
            "conditions on the combined work when statically linked"
        ),
        Category.PROPRIETARY: (
            "custom or commercial license: it cannot be redistributed with your "
            "permissive project without reading the terms"
        ),
        Category.UNKNOWN: (
            "no license declared: your users cannot rely on your permissive terms for "
            "this dependency"
        ),
    },
    Context.COPYLEFT_OK: {
        Category.SOURCE_AVAILABLE: (
            "source-available: not open source, and it forbids exactly the uses the "
            "copyleft ecosystem allows"
        ),
        Category.NON_COMMERCIAL: (
            "non-commercial terms forbid commercial distribution regardless of your "
            "own license"
        ),
        Category.NETWORK_COPYLEFT: (
            "network copyleft: compatible with your copyleft project, but its network "
            "clause reaches your own service"
        ),
        Category.PROPRIETARY: (
            "custom or commercial license: check that it permits copyleft redistribution"
        ),
        Category.UNKNOWN: "no license declared: treat it as all rights reserved until confirmed",
    },
}

# category -> verdict, per context. Anything unlisted is CLEAN.
_RULES: dict[Context, dict[Category, Verdict]] = {
    Context.PROPRIETARY: {
        Category.NETWORK_COPYLEFT: Verdict.BLOCK,
        Category.STRONG_COPYLEFT: Verdict.BLOCK,
        Category.SOURCE_AVAILABLE: Verdict.BLOCK,
        Category.NON_COMMERCIAL: Verdict.BLOCK,
        Category.WEAK_COPYLEFT: Verdict.REVIEW,
        Category.PROPRIETARY: Verdict.REVIEW,
        Category.UNKNOWN: Verdict.REVIEW,
    },
    Context.SAAS_BACKEND: {
        Category.NETWORK_COPYLEFT: Verdict.BLOCK,
        Category.SOURCE_AVAILABLE: Verdict.BLOCK,
        Category.NON_COMMERCIAL: Verdict.BLOCK,
        Category.STRONG_COPYLEFT: Verdict.REVIEW,
        Category.PROPRIETARY: Verdict.REVIEW,
        Category.UNKNOWN: Verdict.REVIEW,
    },
    Context.PERMISSIVE: {
        Category.NETWORK_COPYLEFT: Verdict.BLOCK,
        Category.STRONG_COPYLEFT: Verdict.BLOCK,
        Category.SOURCE_AVAILABLE: Verdict.BLOCK,
        Category.NON_COMMERCIAL: Verdict.BLOCK,
        Category.WEAK_COPYLEFT: Verdict.REVIEW,
        Category.PROPRIETARY: Verdict.REVIEW,
        Category.UNKNOWN: Verdict.REVIEW,
    },
    Context.COPYLEFT_OK: {
        Category.SOURCE_AVAILABLE: Verdict.BLOCK,
        Category.NON_COMMERCIAL: Verdict.BLOCK,
        Category.NETWORK_COPYLEFT: Verdict.REVIEW,
        Category.PROPRIETARY: Verdict.REVIEW,
        Category.UNKNOWN: Verdict.REVIEW,
    },
}

_CONTEXT_ALIASES: dict[str, Context] = {
    "proprietary": Context.PROPRIETARY,
    "closed-source": Context.PROPRIETARY,
    "closed": Context.PROPRIETARY,
    "saas-backend": Context.SAAS_BACKEND,
    "saas": Context.SAAS_BACKEND,
    "internal": Context.SAAS_BACKEND,
    "permissive": Context.PERMISSIVE,
    "open-source": Context.PERMISSIVE,
    "opensource": Context.PERMISSIVE,
    "copyleft-ok": Context.COPYLEFT_OK,
    "copyleft_ok": Context.COPYLEFT_OK,
    "copyleft": Context.COPYLEFT_OK,
}


def normalize_context(value: str | Context | None) -> Context:
    """Accept a context name (or a common alias) and return the enum."""
    if isinstance(value, Context):
        return value
    if value is None or not str(value).strip():
        return Context.PROPRIETARY
    try:
        return _CONTEXT_ALIASES[str(value).strip().lower()]
    except KeyError:
        raise ValueError(f"unknown context {value!r}; choose one of {', '.join(CONTEXTS)}") from None


@dataclass(frozen=True)
class Finding:
    """A license judgment for one dependency in one context."""

    name: str
    license: License
    verdict: Verdict
    reason: str
    context: Context
    version: str | None = None
    ecosystem: str = "python"

    @property
    def blocking(self) -> bool:
        return self.verdict is Verdict.BLOCK

    @property
    def needs_review(self) -> bool:
        return self.verdict is Verdict.REVIEW


def evaluate(
    raw: str | None,
    context: Context | str = Context.PROPRIETARY,
) -> tuple[Verdict, License, str]:
    """Judge a raw license string against a distribution context."""
    ctx = normalize_context(context)
    lic = classify(raw)
    verdict = _RULES[ctx].get(lic.category, Verdict.CLEAN)
    suffix = " (resolved from a multi-license expression)" if lic.expression else ""
    if verdict is Verdict.CLEAN:
        return verdict, lic, f"{lic.id} is fine when distributing as {ctx.value}{suffix}"
    # A rule can exist without a matching explanation; degrade to a generic ask
    # rather than raising inside a tool call.
    reason = _REASONS.get(ctx, {}).get(lic.category) or "flagged for this context: have a human confirm the terms"
    return verdict, lic, f"{lic.id} is {reason}{suffix}"
