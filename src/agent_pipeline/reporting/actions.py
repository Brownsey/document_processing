"""Recorded action wording, funding context and presentation order."""

from agent_pipeline.reporting.accounts import (
    account_index,
    account_label,
    action_label,
    destination_labels,
    money,
)
from agent_pipeline.reporting.confirmations import confirmation_lines, review_items
from agent_pipeline.rules.isa_funding import ISA_DISPOSAL_HOLD


def render_actions(renderer: str, facts: dict, config: dict) -> str:
    accounts = account_index(facts)

    reviews = review_items(facts, config.get("adviser_confirmations", "full"))
    holds = [item for item in reviews if item.get("code") == "isa_funding_conflict"]
    query_lines = confirmation_lines(reviews, holds, accounts)
    lines = (
        [
            "**IMPLEMENTATION PAUSED — adviser clarification required.**\n\n"
            + "\n\n".join(
                "[REVIEW REQUIRED: " + item["message"] + "]" for item in holds
            )
            + "\n\nRecorded plan, subject to that clarification:\n"
        ]
        if holds
        else []
    )
    funding_lines = _funding_lines(facts, accounts)
    if funding_lines:
        lines.append(" ".join(funding_lines) + "\n")
    funding = {r["receipt_id"]: r for r in facts.get("receipts", [])}
    # Stable presentation order; source timing and conditions remain attached.
    priority = {
        "dispose": 0,
        "open": 1,
        "transfer": 2,
        "contribute": 2,
        "rebalance": 3,
        "retain": 4,
    }
    for action in sorted(
        facts.get("actions", []), key=lambda a: priority.get(a["kind"], 5)
    ):
        kind = action["kind"]
        text = _action_text(action, accounts, facts, funding)
        if kind == "confirm":
            query_lines.append(text)
        elif kind == "exclude" or action.get("status") in {"future", "excluded"}:
            lines.append("\n" + text + "\n")
        else:
            lines.append("- " + text)
    if renderer == "adviser_queries":
        return "\n".join(query_lines)
    if renderer == "actions":
        lines.extend(query_lines)
    return "\n".join(lines)


def _funding_lines(facts: dict, accounts: dict) -> list[str]:
    funding_lines = []
    for receipt in facts.get("receipts", []):
        if receipt.get("status") in {"pending", "contingent"}:
            funding_lines.append(
                f"{receipt.get('description', 'Expected proceeds').rstrip('. ')} ({money(receipt.get('amount'))}) is {receipt['status']} and excluded from available funding."
            )
        elif receipt.get("status") == "received" and not receipt.get(
            "source_account_id"
        ):
            funding_lines.append(
                f"Funds received: {receipt.get('description', 'funds').rstrip('. ')}: {money(receipt.get('amount'))}."
            )
    for commitment in facts.get("commitments", []):
        state = "already paid" if commitment.get("status") == "paid" else "reserved"
        funding_lines.append(
            f"{money(commitment.get('amount'))} {state} for {commitment['description'].rstrip('. ')}."
        )
    for balance in facts.get("funding_balances", []):
        funding_lines.append(
            f"Available funding after linked commitments: {money(balance.get('available') or balance.get('amount'))}."
        )
    active = [
        a
        for a in facts.get("actions", [])
        if a.get("status", "agreed") in {"agreed", "conditional"}
    ]
    if active and all(
        a.get("kind") == "transfer"
        and "cash"
        in accounts.get(a.get("source_account_id"), {}).get("account_type", "").lower()
        for a in active
    ):
        funding_lines.append(
            "No investments are being sold; these transfers use existing cash."
        )
    return funding_lines


def _action_text(action: dict, accounts: dict, facts: dict, funding: dict) -> str:
    kind = action["kind"]
    source = action.get("source_account_id")
    destinations = action.get("destination_account_ids", [])
    amount = money(action["amount"]) if action.get("amount") else ""
    extent = action.get("extent", "")
    pooled = (
        kind == "contribute"
        and source
        and action.get("source_funding_ids")
        and all(
            funding[x].get("status") == "received" for x in action["source_funding_ids"]
        )
        and any(
            a.get("kind") == "dispose" and a.get("source_account_id") == source
            for a in facts.get("actions", [])
        )
    )
    if pooled:
        target = (
            destination_labels(destinations, accounts)
            or "destinations requiring confirmation"
        )
        allocation = (
            f"{amount} from the combined funds" if amount else "the combined funds"
        )
        sources = " and ".join(
            funding[x]["description"].rstrip(". ") for x in action["source_funding_ids"]
        )
        text = f"Combine the proceeds from {action_label(source, accounts)} with {sources}. Allocate {allocation} to {target}."
    elif kind in {"rebalance", "dispose", "retain"}:
        target = (
            action_label(source, accounts)
            if source
            else " and ".join(action_label(x, accounts) for x in destinations)
        )
        if kind == "rebalance":
            text = f"Rebalance {amount + ' of ' if amount else ''}{target}."
        else:
            quantity = (
                "the entire holding in "
                if extent == "full"
                else f"{amount} of "
                if amount
                else "the agreed portion of "
                if extent == "partial"
                else ""
            )
            verb = "Sell" if kind == "dispose" else kind.capitalize()
            text = f"{verb} {quantity}{target}."
    elif kind == "open":
        text = (
            "Open "
            + " and ".join(
                "a " + account_label(accounts[x])
                if accounts[x].get("status") == "planned"
                else action_label(x, accounts)
                for x in destinations
            )
            + "."
        )
    elif kind in {"confirm", "exclude"}:
        text = (
            action.get("rationale")
            or (
                "This item is outside the current recommendations"
                if kind == "exclude"
                else "Confirm the outstanding details"
            )
        ).rstrip(". ")
        if source:
            text += f" ({action_label(source, accounts)})"
        if kind == "exclude" and destinations:
            text += f" ({destination_labels(destinations, accounts)})"
        text += "."
    else:
        text = f"{kind.capitalize()} {amount or 'an amount to be confirmed'}"
        if source:
            text += f" from {action_label(source, accounts)}"
        if destinations:
            text += " to " + destination_labels(destinations, accounts)
        text += "."
    if action.get("source_funding_ids") and not pooled:
        text += (
            " Funding: "
            + "; ".join(
                funding[x]["description"].rstrip(". ")
                for x in action["source_funding_ids"]
            )
            + "."
        )
    if action.get("status") in {"future", "excluded"}:
        text = "Not a current recommendation: " + text
    if action.get("allocation_rule"):
        text += " " + action["allocation_rule"].rstrip(". ") + "."
    if action.get("timing"):
        text += " Timing: " + action["timing"].rstrip(". ") + "."
    held = ISA_DISPOSAL_HOLD in action.get("conditions", [])
    conditions = [
        c
        for c in action.get("conditions", [])
        if not (
            held
            and c
            in {
                ISA_DISPOSAL_HOLD,
                "ISA top-ups are subject to each account's remaining allowance",
            }
        )
    ]
    if conditions:
        text += " Conditions: " + "; ".join(c.rstrip(". ") for c in conditions) + "."
    if action.get("status") == "conditional" and not held:
        text += " Do not implement until these conditions are resolved."
    return text
