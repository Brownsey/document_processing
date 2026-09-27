"""Flag full GIA disposals whose proceeds are directed only into ISAs."""

import re
from datetime import date
from decimal import Decimal

from agent_pipeline.rules.models import Account, CaseFacts
from agent_pipeline.rules.provenance import dates
from agent_pipeline.rules.review import add_review

# Adult ISA subscription ceiling, verified 27 September 2026:
# https://www.gov.uk/individual-savings-accounts (2026/27 tax year).
# Unlisted years require adviser confirmation; never roll a limit forward silently.
ISA_ANNUAL_LIMITS = {"2026/27": Decimal("20000")}
ISA_DISPOSAL_HOLD = (
    "Resolve the ISA funding plan with the adviser before the disposal or reinvestment"
)


def check_isa_disposal_plans(facts: CaseFacts, accounts: dict[str, Account]) -> None:
    """Hold ISA-only full-disposal plans for clarification, without changing the advice."""
    active = [a for a in facts.actions if a.status in {"agreed", "conditional"}]
    for sale in active:
        source = accounts.get(sale.source_account_id or "")
        if (
            sale.kind != "dispose"
            or sale.extent != "full"
            or source is None
            or not re.search(
                r"\b(?:GIA|general investment account)\b", source.account_type, re.I
            )
        ):
            continue
        allocations = [
            a
            for a in active
            if a.kind in {"contribute", "transfer"}
            and (
                a.source_account_id == sale.source_account_id
                or any(
                    receipt.receipt_id in a.source_funding_ids
                    and receipt.source_account_id == sale.source_account_id
                    for receipt in facts.receipts
                )
            )
        ]
        target_ids = list(
            dict.fromkeys(
                [
                    *sale.destination_account_ids,
                    *(key for a in allocations for key in a.destination_account_ids),
                ]
            )
        )
        if not target_ids or any(
            key not in accounts
            or not re.search(r"\b(?:ISA|JISA|LISA)\b", accounts[key].account_type, re.I)
            for key in target_ids
        ):
            continue
        targets = [accounts[key] for key in target_ids]
        owners = {
            owner.strip().casefold()
            for a in targets
            for owner in a.owners
            if owner.strip()
        }
        timing = " ".join(a.timing or "" for a in [sale, *allocations])
        stated_years = re.findall(r"20\d{2}[/-](?:20)?\d{2}", timing)
        unsupported_timing = bool(
            re.search(r"\b(?:next|following)\s+(?:tax\s+)?year\b", timing, re.I)
            # Numeric dates have not been resolved by the source-date parser.
            or re.search(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b", timing)
            or any(year.replace("-", "/") != facts.tax_year for year in stated_years)
            or any(
                str(day.year - (day < date(day.year, 4, 6)))
                != (facts.tax_year or "")[:4]
                for day in dates(timing)
            )
        )
        special_wrapper = any(
            re.search(
                r"\b(?:junior|lifetime|JISA|LISA|help\W+to\W+buy)\b",
                a.account_type,
                re.I,
            )
            for a in targets
        )
        limit = (
            ISA_ANNUAL_LIMITS.get(facts.tax_year or "")
            if not unsupported_timing and not special_wrapper
            else None
        )
        supported_ownership = all(
            len(a.owners) == 1 and a.owners[0].strip() for a in targets
        )
        value = sale.amount or source.valuation
        if limit is not None and supported_ownership and owners:
            capacity = limit * len(owners)
            ceiling = f"GBP {capacity:,.0f} maximum combined annual ISA subscription allowance for {facts.tax_year}"
            if (
                value
                and value.currency == "GBP"
                and value.precision in {"exact", "lower_bound"}
                and value.value > capacity
            ):
                observation = (
                    "The recorded disposal amount"
                    if sale.amount
                    else "The latest holding valuation"
                )
                message = f"{observation} exceeds the {ceiling}."
            else:
                message = (
                    f"Confirm whether the disposal proceeds fit within the {ceiling}."
                )
        else:
            message = "Confirm the relevant tax year, applicable ISA subscription limits and each recipient's allowance."
        message += (
            " Existing subscriptions reduce the remaining allowance; a valuation is not guaranteed sale proceeds."
            " Confirm remaining allowances and exact proceeds, then obtain the adviser's decision on a revised sale amount or the treatment of surplus proceeds."
            " Do not implement the disposal or reinvestment until this is resolved."
        )
        add_review(
            facts, "isa_funding_conflict", message, [source.account_id, *target_ids]
        )
        for action in [sale, *allocations]:
            action.status = "conditional"
            if ISA_DISPOSAL_HOLD not in action.conditions:
                action.conditions.append(ISA_DISPOSAL_HOLD)
