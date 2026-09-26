"""Deterministic presentation of reconciled facts; never calculates advice."""

from datetime import date
from decimal import Decimal

from agent_pipeline.validation import RISK_WARNING


def money(value: dict | None, *, dated: bool = False) -> str:
    if not value or value.get("value") is None:
        return "Value not supplied — adviser confirmation required"
    amount = Decimal(str(value["value"]))
    number = f"{amount:,.2f}".removesuffix(".00")
    currency = value.get("currency", "GBP")
    symbol = {"GBP": "£", "EUR": "€", "USD": "$"}.get(currency, currency + " ")
    qualifier = {
        "approximate": "around ",
        "lower_bound": "a little over ",
        "upper_bound": "up to ",
        "unknown": "unconfirmed ",
    }.get(value.get("precision", "exact"), "")
    text = qualifier + symbol + number
    if dated:
        when = value.get("effective_date")
        text += (
            " ("
            + (
                date.fromisoformat(str(when)).strftime("%d %B %Y").lstrip("0")
                if when
                else "valuation date not supplied"
            )
            + ")"
        )
    return text


def account_label(account: dict) -> str:
    return " ".join(
        filter(
            None,
            [
                account.get("platform"),
                account.get("account_type"),
                f"({account['account_id']})",
            ],
        )
    )


def scoped_accounts(facts: dict) -> list[dict]:
    return [
        a
        for a in facts.get("accounts", [])
        if a["account_id"] in facts.get("requested_account_ids", [])
    ]


def taxable_disposals(facts: dict) -> list[dict]:
    accounts = {a["account_id"]: a for a in facts.get("accounts", [])}
    return [
        a
        for a in facts.get("actions", [])
        if a["kind"] in {"dispose", "sale", "disposal"}
        and a.get("status") in {"agreed", "conditional"}
        and not any(
            t
            in accounts.get(a.get("source_account_id"), {})
            .get("account_type", "")
            .lower()
            for t in ("isa", "pension", "sipp")
        )
    ]


def render_slot(renderer: str, facts: dict, config: dict) -> str:
    if renderer == "risk_warning":
        return config.get("risk_warning", RISK_WARNING)
    if renderer == "scope":
        descriptions = [
            f"{account_label(a)} held by {' and '.join(a['owners'])}"
            for a in scoped_accounts(facts)
        ]
        descriptions += [
            f"the proposed {account_label(a)} for {' and '.join(a['owners'])}"
            for a in facts.get("planned_accounts", [])
            if a["account_id"] in facts.get("requested_account_ids", [])
        ]
        return "; ".join(descriptions)
    if renderer == "holdings":
        rows = ["| Account | Owner | Type | Value |", "|---|---|---|---|"]
        for a in scoped_accounts(facts):
            cells = [
                account_label(a),
                " and ".join(a["owners"]),
                a["account_type"],
                money(a.get("valuation"), dated=True),
            ]
            rows.append(
                "| "
                + " | ".join(
                    str(c).replace("|", "\\|").replace("\n", " ") for c in cells
                )
                + " |"
            )
        return "\n".join(rows)
    if renderer == "tax":
        if not taxable_disposals(facts):
            return "No potentially taxable disposal is recommended."
        return "The proposed disposal may create a capital gains tax liability, assessed against the annual exempt amount. Your adviser must confirm acquisition costs, realised gains, available exemptions and any tax due before implementation; no tax estimate is provided."
    if renderer == "fees":
        lines = []
        kinds = set()
        for fee in facts.get("fees", []):
            kind = fee.get("kind", "charge")
            kinds.add(kind)
            rate = fee.get("rate_percent")
            amount = fee.get("amount")
            figure = (
                f"{Decimal(str(rate)):g}%"
                if rate is not None
                else money(amount)
                if amount
                else "adviser confirmation required"
            )
            basis = (
                f" on {fee['basis']}"
                if fee.get("basis")
                else " (basis requires confirmation)"
            )
            lines.append(
                f"{kind.replace('_', ' ').capitalize()} charge: {figure}{basis}."
            )
        for kind, label in (
            ("platform", "Platform"),
            ("ongoing_advice", "Ongoing advice"),
        ):
            if kind not in kinds:
                lines.append(
                    f"{label} charge: adviser confirmation required before implementation."
                )
        return " ".join(lines)
    if renderer == "actions":
        accounts = {
            a["account_id"]: a
            for a in facts.get("accounts", []) + facts.get("planned_accounts", [])
        }

        def label(identifier):
            return (
                account_label(accounts[identifier])
                if identifier in accounts
                else "source/destination requires confirmation"
            )

        lines = []
        verbs = {
            "contribute": "Contribute",
            "contribution": "Contribute",
            "transfer": "Transfer",
            "sale": "Sell",
            "dispose": "Sell",
            "disposal": "Dispose of",
            "retain": "Retain",
            "open": "Open",
            "withdrawal": "Withdraw",
            "investment": "Invest",
        }
        for action in facts.get("actions", []):
            kind = action["kind"]
            source = action.get("source_account_id")
            destinations = action.get("destination_account_ids", [])
            amount = money(action["amount"]) if action.get("amount") else ""
            extent = action.get("extent", "")
            if kind in {"dispose", "sale", "disposal", "retain", "withdrawal"}:
                target = (
                    label(source)
                    if source
                    else " and ".join(label(x) for x in destinations)
                )
                quantity = (
                    "the entire holding in "
                    if extent == "full"
                    else f"{amount} of "
                    if amount
                    else "the agreed portion of "
                    if extent == "partial"
                    else ""
                )
                text = f"{verbs.get(kind, kind.capitalize())} {quantity}{target}."
            elif kind == "open":
                text = "Open " + " and ".join(label(x) for x in destinations) + "."
            else:
                text = f"{verbs.get(kind, kind.capitalize())} {amount or 'the agreed amount (to be confirmed)'}"
                if source:
                    text += f" from {label(source)}"
                if destinations:
                    text += " to " + " and ".join(label(x) for x in destinations)
                text += "."
            funding = {r["receipt_id"]: r for r in facts.get("receipts", [])}
            if action.get("source_funding_ids"):
                text += (
                    " Funding: "
                    + "; ".join(
                        funding[x]["description"] for x in action["source_funding_ids"]
                    )
                    + "."
                )
            if action.get("status") in {"future", "excluded"}:
                text = "Not a current recommendation: " + text
            for field, prefix in (
                ("allocation_rule", "Allocation"),
                ("rationale", "Reason"),
                ("timing", "Timing"),
            ):
                if action.get(field):
                    text += f" {prefix}: {action[field]}."
            if action.get("conditions"):
                text += " Conditions: " + "; ".join(action["conditions"]) + "."
            if action.get("status") in {"conditional", "blocked"}:
                text += " Do not implement until these conditions are resolved."
            lines.append("- " + text)
        for receipt in facts.get("receipts", []):
            if receipt.get("status") == "contingent":
                lines.append(
                    f"- {receipt.get('description', 'Contingent proceeds')} ({money(receipt.get('amount'))}) is contingent and excluded from available funding."
                )
        for commitment in facts.get("commitments", []):
            state = "already paid" if commitment.get("status") == "paid" else "reserved"
            lines.append(
                f"- {money(commitment.get('amount'))} {state} for {commitment['description']}."
            )
        for balance in facts.get("funding_balances", []):
            lines.append(
                f"- Available funding after linked commitments: {money(balance.get('available') or balance.get('amount'))}."
            )
        lines.append(
            "- Relevant platform charges must be confirmed before implementation."
        )
        for item in facts.get("review_items", []):
            lines.append(
                "- Adviser review: "
                + item.get(
                    "message",
                    item.get(
                        "description", "Unresolved evidence requires confirmation"
                    ),
                )
            )
        return "\n".join(lines)
    raise ValueError("Unknown renderer")
