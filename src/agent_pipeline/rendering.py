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
    kind = account.get("account_type", "")
    if account.get("status") == "planned" and kind.casefold().endswith(" (joint)"):
        kind = "joint " + kind[:-8]
    if (
        account.get("status") == "planned"
        and len(account.get("owners", [])) > 1
        and "joint" not in kind.lower()
    ):
        kind = "joint " + kind
    return " ".join(
        filter(
            None,
            [
                account.get("platform"),
                kind,
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
        if a["kind"] == "dispose"
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
        if len(scoped_accounts(facts)) > 4:
            groups: dict[tuple[str, str, bool], int] = {}
            for account in scoped_accounts(facts):
                key = (
                    account.get("platform", ""),
                    account.get("account_type", ""),
                    len(account.get("owners", [])) > 1,
                )
                groups[key] = groups.get(key, 0) + 1
            descriptions = [
                f"{platform} {'joint ' if joint and 'joint' not in kind.lower() else ''}{kind}{'s' if count > 1 else ''}"
                for (platform, kind, joint), count in groups.items()
            ]
            descriptions += [
                "a proposed "
                + (
                    "joint "
                    if len(a.get("owners", [])) > 1
                    and "joint" not in a.get("account_type", "").lower()
                    else ""
                )
                + a.get("account_type", "investment account")
                for a in facts.get("planned_accounts", [])
                if a["account_id"] in facts.get("requested_account_ids", [])
            ]
            return "your " + ", ".join(descriptions)
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
        return "The proposed disposal may create a capital gains tax liability, assessed against the annual exempt amount. Your adviser must confirm acquisition costs, realised gains, available exemptions and any tax due before implementation; no tax estimate is provided. [REVIEW REQUIRED: confirm capital gains tax position and amount]"
    if renderer == "fees":
        lines = []
        kinds = set()
        accounts = {
            a["account_id"]: a
            for a in facts.get("accounts", []) + facts.get("planned_accounts", [])
        }
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
                else ""
                if kind == "initial" and rate is not None and Decimal(str(rate)) == 0
                else " (basis requires confirmation)"
            )
            coverage = ", ".join(
                account_label(accounts[key])
                for key in fee.get("account_ids", [])
                if key in accounts
            )
            lines.append(
                f"{kind.replace('_', ' ').capitalize()} charge: {figure}{basis}"
                + (f" for {coverage}" if coverage else "")
                + "."
            )
        for kind, label in (
            ("platform", "Platform"),
            ("ongoing_advice", "Ongoing advice"),
        ):
            if kind not in kinds:
                lines.append(
                    f"{label} charge: adviser confirmation required before finalising."
                )
        platforms = list(
            dict.fromkeys(
                a.get("platform", "")
                for a in scoped_accounts(facts)
                if a.get("platform")
            )
        )
        if platforms:
            lines.append("Relevant platforms: " + ", ".join(platforms) + ".")
        if facts.get("planned_accounts"):
            lines.append("Charges for planned accounts also require confirmation.")
        for kind, label in (
            ("platform", "platform"),
            ("ongoing_advice", "ongoing advice"),
        ):
            confirmed = [
                fee
                for fee in facts.get("fees", [])
                if fee.get("kind") == kind
                and fee.get("confirmed")
                and (fee.get("rate_percent") is not None or fee.get("amount"))
            ]
            covered = {key for fee in confirmed for key in fee.get("account_ids", [])}
            pending = [
                a for a in scoped_accounts(facts) if a["account_id"] not in covered
            ]
            if pending:
                lines.append(
                    f"[REVIEW REQUIRED: confirm {label} charge rate, basis and coverage for "
                    + ", ".join(account_label(a) for a in pending)
                    + "]"
                )
            elif not any(
                fee.get("kind") == kind
                and fee.get("confirmed")
                and (fee.get("rate_percent") is not None or fee.get("amount"))
                for fee in facts.get("fees", [])
            ):
                lines.append(
                    f"[REVIEW REQUIRED: confirm {label} charge rate and basis]"
                )
        return " ".join(lines)
    if renderer == "actions":
        accounts = {
            a["account_id"]: a
            for a in facts.get("accounts", []) + facts.get("planned_accounts", [])
        }

        def action_label(identifier):
            return (
                account_label(accounts[identifier])
                if identifier in accounts
                else "source/destination requires confirmation"
            )

        def destination_labels(identifiers):
            targets = [accounts.get(identifier, {}) for identifier in identifiers]
            if len(targets) > 1 and all(
                target.get("status") != "planned"
                and target.get("account_type")
                and (target.get("platform"), target.get("account_type"))
                == (targets[0].get("platform"), targets[0].get("account_type"))
                for target in targets
            ):
                kind = targets[0]["account_type"]
                plural = kind if kind.endswith("s") else kind + "s"
                return (
                    f"{targets[0].get('platform', '')} {plural} "
                    f"({' and '.join(identifiers)})"
                ).strip()
            return " and ".join(action_label(identifier) for identifier in identifiers)

        lines = []
        verbs = {
            "contribute": "Contribute",
            "transfer": "Transfer",
            "dispose": "Sell",
            "retain": "Retain",
            "open": "Open",
        }
        funding = {r["receipt_id"]: r for r in facts.get("receipts", [])}
        for action in facts.get("actions", []):
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
                    funding[x].get("status") == "received"
                    for x in action["source_funding_ids"]
                )
                and any(
                    a.get("kind") == "dispose" and a.get("source_account_id") == source
                    for a in facts.get("actions", [])
                )
            )
            if pooled:
                target = (
                    destination_labels(destinations)
                    or "destinations requiring confirmation"
                )
                allocation = (
                    f"{amount} from the combined funds"
                    if amount
                    else "the combined funds"
                )
                text = f"Combine the proceeds from {action_label(source)} with the linked received funds. Allocate {allocation} to {target}."
            elif kind == "rebalance":
                target = (
                    action_label(source)
                    if source
                    else " and ".join(action_label(x) for x in destinations)
                )
                text = f"Rebalance {amount + ' of ' if amount else ''}{target}."
            elif kind in {"dispose", "retain"}:
                target = (
                    action_label(source)
                    if source
                    else " and ".join(action_label(x) for x in destinations)
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
                text = (
                    "Open " + " and ".join(action_label(x) for x in destinations) + "."
                )
            elif kind == "confirm":
                text = (
                    action.get("rationale") or "Confirm the outstanding details"
                ).rstrip(". ")
                if source:
                    text += f" ({action_label(source)})"
                text += "."
            else:
                text = f"{verbs.get(kind, kind.capitalize())} {amount or 'an amount to be confirmed'}"
                if source:
                    text += f" from {action_label(source)}"
                if destinations:
                    text += " to " + destination_labels(destinations)
                text += "."
            if action.get("source_funding_ids"):
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
            for field, prefix in (
                ("allocation_rule", "Allocation"),
                ("timing", "Timing"),
            ):
                if action.get(field):
                    text += f" {prefix}: {action[field].rstrip('. ')}."
            if action.get("conditions"):
                text += (
                    " Conditions: "
                    + "; ".join(c.rstrip(". ") for c in action["conditions"])
                    + "."
                )
            if action.get("status") == "conditional":
                text += " Do not implement until these conditions are resolved."
            lines.append("- " + text)
        active = [
            a
            for a in facts.get("actions", [])
            if a.get("status", "agreed") in {"agreed", "conditional"}
        ]
        if active and all(
            a.get("kind") == "transfer"
            and "cash"
            in accounts.get(a.get("source_account_id"), {})
            .get("account_type", "")
            .lower()
            for a in active
        ):
            lines.append(
                "- No investments are being sold; these transfers use existing cash."
            )
        for receipt in facts.get("receipts", []):
            if receipt.get("status") == "contingent":
                lines.append(
                    f"- {receipt.get('description', 'Contingent proceeds').rstrip('. ')} ({money(receipt.get('amount'))}) is contingent and excluded from available funding."
                )
            elif receipt.get("status") == "received" and not receipt.get(
                "source_account_id"
            ):
                lines.append(
                    f"- Received {receipt.get('description', 'funds').rstrip('. ')}: {money(receipt.get('amount'))}."
                )
        for commitment in facts.get("commitments", []):
            state = "already paid" if commitment.get("status") == "paid" else "reserved"
            lines.append(
                f"- {money(commitment.get('amount'))} {state} for {commitment['description'].rstrip('. ')}."
            )
        for balance in facts.get("funding_balances", []):
            lines.append(
                f"- Available funding after linked commitments: {money(balance.get('available') or balance.get('amount'))}."
            )
        for item in facts.get("review_items", []):
            if item.get("code") in {"platform_fees", "ongoing_advice_fees"}:
                continue
            lines.append(
                "- [REVIEW REQUIRED: "
                + item.get(
                    "message",
                    item.get(
                        "description", "Unresolved evidence requires confirmation"
                    ),
                ).rstrip(". ")
                + (
                    " ("
                    + ", ".join(
                        action_label(x)
                        for x in item.get("account_ids", [])
                        if x in accounts
                    )
                    + ")"
                    if any(x in accounts for x in item.get("account_ids", []))
                    else ""
                )
                + ".]"
            )
        return "\n".join(lines)
    raise ValueError("Unknown renderer")
