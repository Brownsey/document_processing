"""Check planned accounts, existing holdings, ownership, and valuations."""

import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal

from .matching import REVIEW, _contains, _normal, money_values


def check_accounts(
    report: str,
    facts: dict | None,
    expected: dict,
    check: Callable[[str, str, object], None],
) -> None:
    rows = [
        line
        for line in report.splitlines()
        if line.lstrip().startswith("|")
        and "---" not in line
        and not re.search(r"\|\s*Account\s*\|", line, re.I)
    ]
    planned_rows = [
        r
        for r in rows
        if re.match(r"\|\s*(?:proposed|planned)\b", r, re.I)
        and re.search(r"\|\s*not (?:yet )?opened\s*\|\s*$", r, re.I)
    ]
    _check_planned_accounts(planned_rows, facts, expected, check)
    existing_rows = [row for row in rows if row not in planned_rows]
    _check_existing_holdings(existing_rows, expected, check)
    for excluded in expected.get("excluded_accounts", []):
        check(
            "accounts",
            f"excluded holding {excluded}",
            not any(_contains(row, [excluded]) for row in rows),
        )


def _check_planned_accounts(
    planned_rows: list[str],
    facts: dict | None,
    expected: dict,
    check: Callable[[str, str, object], None],
) -> None:
    openings = [a for a in expected["actions"] if a["kind"] == "open"]
    supported_planned = [
        a
        for a in (facts or {}).get("planned_accounts", [])
        if a["account_id"] in (facts or {}).get("requested_account_ids", [])
    ]
    matched_planned = set()
    check("accounts", "planned account row count", len(planned_rows) == len(openings))
    for row in planned_rows:
        cells = [
            c.strip().replace(r"\|", "|") for c in re.split(r"(?<!\\)\|", row)[1:-1]
        ]
        matched = None
        for a in supported_planned:
            label = f"{a.get('platform', '')} {a.get('account_type', '')}"
            if (
                len(a.get("owners", [])) > 1
                and "joint" not in a.get("account_type", "").lower()
            ):
                label += " joint"
            if (
                len(cells) == 4
                and a["account_id"] not in matched_planned
                and _normal(cells[1]) == _normal(" and ".join(a.get("owners", [])))
                and _normal(cells[2]) == _normal(a.get("account_type", ""))
                and sorted(re.findall(r"\w+", _normal(cells[0]))[1:])
                == sorted(re.findall(r"\w+", _normal(label)))
            ):
                matched = a
                break
        if matched:
            matched_planned.add(matched["account_id"])
        check(
            "accounts",
            "planned row is authorised and has no current value",
            matched is not None
            and any(_contains(cells[0], a.get("destination", [])) for a in openings)
            and not money_values(row)
            and bool(re.search(r"not (?:yet )?opened", row, re.I)),
        )
    check(
        "accounts",
        "scoped planned accounts appear once",
        matched_planned == {a["account_id"] for a in supported_planned},
    )
    for opening in openings:
        check(
            "accounts",
            "planned opening appears in table",
            any(_contains(row, opening.get("destination", [])) for row in planned_rows),
        )


def _check_existing_holdings(
    existing_rows: list[str], expected: dict, check: Callable[[str, str, object], None]
) -> None:
    check(
        "accounts",
        "holdings row count",
        len(existing_rows) == len(expected["accounts"]),
    )
    for account in expected["accounts"]:
        matching = [r for r in existing_rows if _contains(r, account["aliases"])]
        check("accounts", f"{account['id']} appears once", len(matching) == 1)
        row = matching[0] if matching else ""
        check(
            "accounts",
            f"{account['id']} ownership",
            all(
                _contains(row, [owner, owner.split()[0]]) for owner in account["owners"]
            ),
        )
        if account.get("value") is not None:
            check(
                "valuations",
                f"{account['id']} value",
                money_values(row) == [Decimal(account["value"])],
            )
        else:
            check(
                "valuations",
                f"{account['id']} unknown value",
                not money_values(row) and bool(REVIEW.search(row)),
            )
        precision = account.get("precision", "exact")
        if precision != "exact":
            pattern = (
                r"around|approximately|approx\.?|about"
                if precision == "approximate"
                else r"little over|slightly (?:over|above)|just over|more than"
            )
            check(
                "valuations",
                f"{account['id']} precision",
                bool(re.search(pattern, row, re.I)),
            )
        if account.get("date"):
            d = date.fromisoformat(account["date"])
            dates = [
                d.isoformat(),
                f"{d.day} {d:%B %Y}",
                f"{d.day} {d:%b %Y}",
                f"{d:%d/%m/%Y}",
            ]
            check(
                "valuations", f"{account['id']} effective date", _contains(row, dates)
            )
