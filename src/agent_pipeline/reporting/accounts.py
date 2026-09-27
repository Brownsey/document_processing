"""Account labels, values and report account sections."""

from datetime import date
from decimal import Decimal


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


def account_index(facts: dict) -> dict[str, dict]:
    """Look up existing and proposed accounts with their presentation status."""
    return {
        a["account_id"]: a
        for a in facts.get("accounts", [])
        + [a | {"status": "planned"} for a in facts.get("planned_accounts", [])]
    }


def scoped_accounts(facts: dict, *, planned: bool = False) -> list[dict]:
    """Select existing accounts by default, or proposed accounts when requested."""
    return [
        a | {"status": "planned"} if planned else a
        for a in facts.get("planned_accounts" if planned else "accounts", [])
        if a["account_id"] in facts.get("requested_account_ids", [])
    ]


def action_label(identifier: str, accounts: dict) -> str:
    return (
        ("the proposed " if accounts[identifier].get("status") == "planned" else "")
        + account_label(accounts[identifier])
        if identifier in accounts
        else "source/destination requires confirmation"
    )


def destination_labels(identifiers: list[str], accounts: dict) -> str:
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
            f"{targets[0].get('platform', '')} {plural} ({' and '.join(identifiers)})"
        ).strip()
    return " and ".join(
        action_label(identifier, accounts) for identifier in identifiers
    )


def render_scope(facts: dict) -> str:
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
        f"the proposed {account_label(a)}" for a in scoped_accounts(facts, planned=True)
    ]
    return "your " + (
        ", ".join(descriptions[:-1]) + " and " + descriptions[-1]
        if len(descriptions) > 1
        else "".join(descriptions)
    )


def render_holdings(facts: dict) -> str:
    rows = ["| Account | Owner | Type | Value |", "|---|---|---|---|"]
    for a in scoped_accounts(facts) + scoped_accounts(facts, planned=True):
        unopened = a.get("status") == "planned"
        cells = [
            ("Proposed " if unopened else "") + account_label(a, include_owners=False),
            " and ".join(a["owners"]),
            a["account_type"],
            "Not yet opened" if unopened else money(a.get("valuation"), dated=True),
        ]
        rows.append(
            "| "
            + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in cells)
            + " |"
        )
    return "\n".join(rows)
