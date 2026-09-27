"""Compare manifest facts with independently reviewed expectations."""

import json
from collections.abc import Callable

from .matching import _contains, _same_amount


def check_extraction(
    manifest: dict, expected: dict, check: Callable[[str, str, object], None]
) -> None:
    check(
        "manifest",
        "generation succeeded",
        manifest.get("status") == "needs_review"
        and manifest.get("validation", {}).get("passed") is True,
    )
    facts = manifest.get("facts")
    if facts is not None:
        _check_extracted_accounts(facts, expected, check)
        _check_extracted_actions(facts, expected, check)


def _check_extracted_accounts(
    facts: dict, expected: dict, check: Callable[[str, str, object], None]
) -> None:
    expected_ids = {a["id"] for a in expected["accounts"]}
    existing = {a["account_id"]: a for a in facts.get("accounts", [])}
    scoped_existing = set(facts.get("requested_account_ids", [])) & existing.keys()
    check(
        "extraction",
        "scoped existing account identities",
        scoped_existing == expected_ids,
    )
    for account in expected["accounts"]:
        actual = existing.get(account["id"], {})
        check(
            "extraction",
            f"{account['id']} owners",
            set(actual.get("owners", [])) == set(account["owners"]),
        )
        valuation = actual.get("valuation") or {}
        check(
            "extraction",
            f"{account['id']} valuation",
            _same_amount(valuation.get("value"), account.get("value"))
            and valuation.get("effective_date") == account.get("date")
            and valuation.get("precision", "exact")
            == account.get("precision", "exact"),
        )


def _check_extracted_actions(
    facts: dict, expected: dict, check: Callable[[str, str, object], None]
) -> None:
    expected_ids = {account["id"] for account in expected["accounts"]}
    existing = {account["account_id"]: account for account in facts.get("accounts", [])}
    for index, action in enumerate(expected["actions"]):
        required_destinations = set(action.get("destination", [])) & expected_ids
        possible = []
        for actual in facts.get("actions", []):
            if actual.get("status") not in {"agreed", "conditional"}:
                continue
            kind = {"dispose": "sell"}.get(actual.get("kind"), actual.get("kind"))
            if kind != action["kind"]:
                continue
            source = actual.get("source_account_id") or ""
            destinations = actual.get("destination_account_ids", [])
            source_text = source + " " + json.dumps(existing.get(source, {}))
            destination_text = _destination_text(destinations, existing, facts)
            possible.append(
                (not action.get("source") or _contains(source_text, action["source"]))
                and (
                    bool(required_destinations.intersection(destinations))
                    if required_destinations
                    else not action.get("destination")
                    or _contains(destination_text, action["destination"])
                )
                and (
                    not action.get("amount")
                    or _same_amount(
                        (actual.get("amount") or {}).get("value"), action["amount"]
                    )
                )
                and (
                    not action.get("extent") or actual.get("extent") == action["extent"]
                )
            )
        check("extraction", f"agreed action {index + 1}", any(possible))


def _destination_text(destinations: list[str], existing: dict, facts: dict) -> str:
    destination_text = (
        " ".join(destinations)
        + " "
        + json.dumps([existing.get(i, {}) for i in destinations])
    )
    for planned in facts.get("planned_accounts", []):
        if planned["account_id"] in destinations:
            destination_text += " " + json.dumps(planned)
            owners = {
                owner.strip().casefold()
                for owner in planned.get("owners", [])
                if owner.strip()
            }
            if len(owners) > 1:
                destination_text += " joint account joint " + planned.get(
                    "account_type", "account"
                )
    # Account type plurals express allocations across multiple existing accounts.
    destination_text += (
        " ISAs"
        if len(destinations) > 1 and all("ISA" in i.upper() for i in destinations)
        else ""
    )
    destination_text += (
        " SIPPs"
        if len(destinations) > 1 and all("SIPP" in i.upper() for i in destinations)
        else ""
    )
    return destination_text
