"""Deterministic presentation of reconciled facts; never calculates advice."""

import re
from datetime import date
from decimal import Decimal

from agent_pipeline.domain import ISA_DISPOSAL_HOLD
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


def account_label(account: dict, *, include_owners: bool = True) -> str:
    kind = account.get("account_type", "")
    if account.get("status") == "planned" and kind.casefold().endswith(" (joint)"):
        kind = "joint " + kind[:-8]
    if (
        account.get("status") == "planned"
        and len(account.get("owners", [])) > 1
        and "joint" not in kind.lower()
    ):
        kind = "joint " + kind
    if account.get("status") == "planned":
        kind = kind.replace("Investment account", "investment account")
        if not kind.isupper():
            kind = kind[:1].lower() + kind[1:]
    return " ".join(
        filter(
            None,
            [
                account.get("platform"),
                kind,
                f"({account['account_id']})"
                if account.get("status") != "planned"
                else "for " + " and ".join(account["owners"])
                if include_owners and account.get("owners")
                else "",
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
        groups = {}
        scoped = scoped_accounts(facts)
        for account in scoped:
            key = (
                account.get("platform", ""),
                account.get("account_type", ""),
                len(account.get("owners", [])) > 1,
            )
            groups.setdefault(key, []).append(account["account_id"])
        descriptions = []
        for (platform, kind, joint), identifiers in groups.items():
            label = f"{platform} {'joint ' if joint and 'joint' not in kind.lower() else ''}{kind}{'s' if len(identifiers) > 1 and not kind.endswith('s') else ''}".strip()
            if len(scoped) <= 4:
                label += " (" + " and ".join(identifiers) + ")"
            descriptions.append(label)
        descriptions += [
            f"the proposed {account_label(a | {'status': 'planned'})}"
            for a in facts.get("planned_accounts", [])
            if a["account_id"] in facts.get("requested_account_ids", [])
        ]
        return "your " + (
            ", ".join(descriptions[:-1]) + " and " + descriptions[-1]
            if len(descriptions) > 1
            else "".join(descriptions)
        )
    if renderer == "holdings":
        rows = ["| Account | Owner | Type | Value |", "|---|---|---|---|"]
        planned = [
            a | {"status": "planned"}
            for a in facts.get("planned_accounts", [])
            if a["account_id"] in facts.get("requested_account_ids", [])
        ]
        for a in scoped_accounts(facts) + planned:
            unopened = a.get("status") == "planned"
            cells = [
                ("Proposed " if unopened else "")
                + account_label(a, include_owners=False),
                " and ".join(a["owners"]),
                a["account_type"],
                "Not yet opened" if unopened else money(a.get("valuation"), dated=True),
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
        accounts = {
            a["account_id"]: a
            for a in facts.get("accounts", [])
            + [a | {"status": "planned"} for a in facts.get("planned_accounts", [])]
        }
        for fee in facts.get("fees", []):
            kind = fee.get("kind", "charge")
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
        platforms = list(
            dict.fromkeys(
                a.get("platform", "")
                for a in scoped_accounts(facts)
                if a.get("platform")
            )
        )
        covered_accounts = scoped_accounts(facts) + [
            a | {"status": "planned"}
            for a in facts.get("planned_accounts", [])
            if a["account_id"] in facts.get("requested_account_ids", [])
        ]
        pending_charges = {}
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
            pending = tuple(
                a["account_id"]
                for a in covered_accounts
                if a["account_id"] not in covered
            )
            if pending or not confirmed:
                pending_charges.setdefault(pending, []).append(label)
        for identifiers, labels in pending_charges.items():
            if identifiers and set(identifiers) == {
                a["account_id"] for a in covered_accounts
            }:
                coverage = "all accounts covered by this report"
                if platforms:
                    coverage += " (existing platforms: " + ", ".join(platforms) + ")"
                if any(a.get("status") == "planned" for a in covered_accounts):
                    coverage += ", including the proposed accounts"
            else:
                coverage = ", ".join(
                    account_label(accounts[key]) for key in identifiers
                )
            charges = " and ".join(label + " charge" for label in labels)
            lines.append(
                f"[REVIEW REQUIRED: confirm {charges} rate, basis and coverage"
                + (f" for {coverage}" if coverage else "")
                + "]"
            )
        return " ".join(lines)
    if renderer in {"actions", "action_plan", "adviser_queries"}:
        accounts = {
            a["account_id"]: a
            for a in facts.get("accounts", [])
            + [a | {"status": "planned"} for a in facts.get("planned_accounts", [])]
        }

        def action_label(identifier):
            return (
                (
                    "the proposed "
                    if accounts[identifier].get("status") == "planned"
                    else ""
                )
                + account_label(accounts[identifier])
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

        reviews = _review_items(facts, config.get("adviser_confirmations", "full"))
        holds = [item for item in reviews if item.get("code") == "isa_funding_conflict"]
        query_lines = []
        outstanding = [item for item in reviews if item not in holds]
        if outstanding:
            query_lines.append("\n**Adviser confirmations:**\n")
        for item in outstanding:
            query_lines.append(
                "[REVIEW REQUIRED: "
                + item.get(
                    "message",
                    item.get(
                        "description", "Unresolved evidence requires confirmation"
                    ),
                ).rstrip(". ")
                + (
                    " ("
                    + destination_labels(
                        [x for x in item.get("account_ids", []) if x in accounts]
                    )
                    + ")"
                    if any(x in accounts for x in item.get("account_ids", []))
                    else ""
                )
                + ".]\n"
            )
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
            in accounts.get(a.get("source_account_id"), {})
            .get("account_type", "")
            .lower()
            for a in active
        ):
            funding_lines.append(
                "No investments are being sold; these transfers use existing cash."
            )
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
                sources = " and ".join(
                    funding[x]["description"].rstrip(". ")
                    for x in action["source_funding_ids"]
                )
                text = f"Combine the proceeds from {action_label(source)} with {sources}. Allocate {allocation} to {target}."
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
                verb = "Sell" if kind == "dispose" else kind.capitalize()
                text = f"{verb} {quantity}{target}."
            elif kind == "open":
                text = (
                    "Open "
                    + " and ".join(
                        "a " + account_label(accounts[x])
                        if accounts[x].get("status") == "planned"
                        else action_label(x)
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
                    text += f" ({action_label(source)})"
                if kind == "exclude" and destinations:
                    text += f" ({destination_labels(destinations)})"
                text += "."
            else:
                text = f"{kind.capitalize()} {amount or 'an amount to be confirmed'}"
                if source:
                    text += f" from {action_label(source)}"
                if destinations:
                    text += " to " + destination_labels(destinations)
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
            for field, prefix in (
                ("allocation_rule", "Allocation"),
                ("timing", "Timing"),
            ):
                if action.get(field):
                    text += (
                        " " + action[field].rstrip(". ") + "."
                        if field == "allocation_rule"
                        else f" {prefix}: {action[field].rstrip('. ')}."
                    )
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
                text += (
                    " Conditions: "
                    + "; ".join(c.rstrip(". ") for c in conditions)
                    + "."
                )
            if action.get("status") == "conditional" and not held:
                text += " Do not implement until these conditions are resolved."
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
    raise ValueError("Unknown renderer")


def _review_items(facts: dict, mode: str = "full") -> list[dict]:
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
