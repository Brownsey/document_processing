"""Group adviser confirmations for report presentation."""

import re

from agent_pipeline.reporting.accounts import destination_labels


def review_items(facts: dict, mode: str = "full") -> list[dict]:
    # Merge presentation only; the manifest retains every original review item.
    candidates = facts.get("review_items", []) + [
        item | {"category": "discrepancy"} for item in facts.get("conflicts", [])
    ]
    items = [
        dict(item)
        for item in candidates
        if item.get("code") not in {"platform_fees", "ongoing_advice_fees"}
        and (
            mode == "full"
            or item.get("category") == "discrepancy"
            or item.get("code") == "isa_funding_conflict"
            or item.get("blocking")
        )
    ]
    held_accounts = {
        key
        for item in items
        if item.get("code") == "isa_funding_conflict"
        for key in item.get("account_ids", [])
    }
    items = [
        item
        for item in items
        if not (
            item.get("code") in {"isa_capacity", "allocation_confirmation"}
            and item.get("message")
            in {
                "Confirm existing subscriptions, remaining ISA capacity and treatment of excess proceeds before implementation.",
                "Confirm existing subscriptions and each recipient's remaining ISA allowance for the relevant tax year before implementation.",
                "Confirm exact available funding and allocation amounts before implementation.",
                "Confirm allocation amounts before implementation.",
            }
            and not item.get("blocking")
            and item.get("account_ids")
            and set(item["account_ids"]) <= held_accounts
        )
    ]
    redundant = set()
    for index, item in enumerate(items):
        if (
            item.get("code") != "missing_balance"
            or not item.get("account_ids")
            or item.get("blocking")
        ):
            continue
        for other in items:
            message = other.get("message", "")
            if (
                other.get("code") != "missing_balance"
                and not other.get("blocking")
                and set(other.get("account_ids", [])) == set(item["account_ids"])
                and re.match(r"(?:confirm|verify|establish)\b", message, re.I)
                and re.search(r"\bbalance\b", message, re.I)
                and not re.search(
                    r"\b(?:not|no|never|after|until|next|when|once|unless|only|if|before|later|during|on|by|at|fee|charge|basis)\b",
                    message,
                    re.I,
                )
                and (
                    "status" not in item.get("message", "").lower()
                    or re.search(r"\b(?:status|active)\b", message, re.I)
                )
            ):
                other["message"] = message.rstrip(". ") + " before finalising."
                redundant.add(index)
                break
    grouped = {}
    for index, item in enumerate(items):
        if index in redundant:
            continue
        message = item.get(
            "message",
            item.get("description", "Unresolved evidence requires confirmation"),
        )
        key = (message.strip().rstrip(".").casefold(), item.get("blocking", False))
        if key not in grouped:
            grouped[key] = dict(item, account_ids=[])
        grouped[key]["account_ids"] = list(
            dict.fromkeys(grouped[key]["account_ids"] + item.get("account_ids", []))
        )
    return list(grouped.values())


def confirmation_lines(
    reviews: list[dict], holds: list[dict], accounts: dict
) -> list[str]:
    query_lines = []
    outstanding = [item for item in reviews if item not in holds]
    if outstanding:
        query_lines.append("\n**Adviser confirmations:**\n")
    for item in outstanding:
        query_lines.append(
            "[REVIEW REQUIRED: "
            + item.get(
                "message",
                item.get("description", "Unresolved evidence requires confirmation"),
            ).rstrip(". ")
            + (
                " ("
                + destination_labels(
                    [x for x in item.get("account_ids", []) if x in accounts], accounts
                )
                + ")"
                if any(x in accounts for x in item.get("account_ids", []))
                else ""
            )
            + ".]\n"
        )
    return query_lines
