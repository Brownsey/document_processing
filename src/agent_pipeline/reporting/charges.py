"""Tax and fee sections from recorded facts."""

from decimal import Decimal

from agent_pipeline.reporting.accounts import (
    account_index,
    account_label,
    money,
    scoped_accounts,
)


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


def render_tax(facts: dict) -> str:
    if not taxable_disposals(facts):
        return "No potentially taxable disposal is recommended."
    return "The proposed disposal may create a capital gains tax liability, assessed against the annual exempt amount. Your adviser must confirm acquisition costs, realised gains, available exemptions and any tax due before implementation; no tax estimate is provided. [REVIEW REQUIRED: confirm capital gains tax position and amount]"


def render_fees(facts: dict) -> str:
    lines = []
    accounts = account_index(facts)
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
            a.get("platform", "") for a in scoped_accounts(facts) if a.get("platform")
        )
    )
    covered_accounts = scoped_accounts(facts) + scoped_accounts(facts, planned=True)
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
            a["account_id"] for a in covered_accounts if a["account_id"] not in covered
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
            coverage = ", ".join(account_label(accounts[key]) for key in identifiers)
        charges = " and ".join(label + " charge" for label in labels)
        lines.append(
            f"[REVIEW REQUIRED: confirm {charges} rate, basis and coverage"
            + (f" for {coverage}" if coverage else "")
            + "]"
        )
    return " ".join(lines)
