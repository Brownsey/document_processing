"""Pure, source-reviewed scoring of rendered reports."""

from __future__ import annotations

from collections import defaultdict

from .accounts import check_accounts
from .actions import check_actions
from .content import (
    check_fees,
    check_funding,
    check_narrative,
    check_review,
    check_tax,
    check_unsupported,
)
from .extraction import check_extraction
from .structure import check_structure

SCORER_VERSION = "source-reviewed-v6-planned-table"


def score_report(
    report: str, manifest: dict, expected: dict, config: dict | None = None
) -> dict:
    """Score actual Markdown; a self-reported passing manifest cannot mask corruption."""
    checks: list[dict] = []

    def check(category: str, name: str, passed: object) -> None:
        checks.append({"category": category, "name": name, "passed": bool(passed)})

    check(
        "validation",
        "no publication override",
        not manifest.get("published_anyway", False),
    )
    sections = check_structure(report, expected, config, check)
    check_extraction(manifest, expected, check)
    check_accounts(report, manifest.get("facts"), expected, check)
    check_actions(sections["recommendations"], expected, check)
    check_narrative(report, expected, check)
    check_funding(sections["recommendations"], expected, check)
    check_review(report, expected, check)
    check_tax(sections["tax_implications"], expected, check)
    check_fees(report, sections["fees_charges"], expected, check)
    check_unsupported(report, expected, check)

    grouped: dict[str, list[bool]] = defaultdict(list)
    for item in checks:
        grouped[item["category"]].append(item["passed"])
    return {
        "scorer_version": SCORER_VERSION,
        "passed": all(c["passed"] for c in checks),
        "score": sum(c["passed"] for c in checks) / len(checks),
        "categories": {k: sum(v) / len(v) for k, v in grouped.items()},
        "checks": checks,
        "failures": [c["name"] for c in checks if not c["passed"]],
        "human_review": "required",
    }
