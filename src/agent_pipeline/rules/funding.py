"""Calculate receipt, disposal and account funds after reserving commitments."""

import json
from decimal import Decimal

from agent_pipeline.contracts import Err, Ok, PipelineError, Result
from agent_pipeline.rules.allocations import check_outflows, check_source_allocations
from agent_pipeline.rules.models import (
    Account,
    CaseFacts,
    FundingBalance,
    Money,
    Receipt,
)
from agent_pipeline.rules.provenance import normal
from agent_pipeline.rules.review import add_review, failure


def prepare_funding(facts: CaseFacts) -> Result[dict[str, Receipt], PipelineError]:
    """Validate financial event identities and calculate exact external receipt balances."""
    receipt_map = {receipt.receipt_id: receipt for receipt in facts.receipts}
    if len(receipt_map) != len(facts.receipts):
        return failure("duplicate_receipt", "Receipt identities must be unique.")
    if len({item.commitment_id for item in facts.commitments}) != len(
        facts.commitments
    ):
        return failure("duplicate_commitment", "Commitment identities must be unique.")
    for events, identity in [
        (facts.receipts, "receipt_id"),
        (facts.commitments, "commitment_id"),
    ]:
        seen: set[str] = set()
        for event in events:
            signature = event.model_dump(mode="json", exclude={identity, "refs"})
            signature["description"] = normal(signature["description"]).casefold()
            key = json.dumps(signature, sort_keys=True)
            if key in seen:
                return failure(
                    "duplicate_economic_event",
                    "Matching financial events need deduplication before funds can be calculated.",
                    blocked=True,
                )
            seen.add(key)
    if any(item.receipt_id not in receipt_map for item in facts.commitments):
        return failure(
            "unknown_receipt", "A commitment has no matching receipt.", blocked=True
        )
    for receipt in facts.receipts:
        if receipt.status != "received" or receipt.source_account_id:
            continue
        if receipt.amount is None or receipt.amount.precision != "exact":
            add_review(
                facts,
                "qualified_funding",
                "Confirm the exact received amount before allocating funds.",
            )
            continue
        commitments = [
            x
            for x in facts.commitments
            if x.receipt_id == receipt.receipt_id and not x.already_reflected
        ]
        if any(x.amount is None or x.amount.precision != "exact" for x in commitments):
            add_review(
                facts,
                "unknown_commitment",
                "Confirm the commitment amount before calculating available funds.",
            )
            continue
        if any(
            x.amount and x.amount.currency != receipt.amount.currency
            for x in commitments
        ):
            return failure(
                "currency_conflict",
                "A receipt and commitment have incompatible currencies.",
                blocked=True,
            )
        available = receipt.amount.value - sum(
            (x.amount.value for x in commitments if x.amount), Decimal(0)
        )
        if available < 0:
            return failure(
                "insufficient_funds",
                "Commitments exceed the linked received funds.",
                blocked=True,
            )
        facts.funding_balances.append(
            FundingBalance(
                receipt_id=receipt.receipt_id,
                available=receipt.amount.model_copy(update={"value": available}),
                deducted_commitment_ids=[x.commitment_id for x in commitments],
            )
        )
    return Ok(receipt_map)


def check_allocations(
    facts: CaseFacts, accounts: dict[str, Account]
) -> Result[None, PipelineError]:
    """Check receipt, sale, and account routes against their shared funding capacity."""
    available = {
        balance.receipt_id: balance.available for balance in facts.funding_balances
    }
    sales = _sale_limits(facts, accounts)
    if isinstance(sales, Err):
        return sales
    sale_limits = sales.value
    internal = _add_internal_receipts(facts, accounts, sale_limits, available)
    if isinstance(internal, Err):
        return internal
    receipt_sources = {
        receipt.receipt_id: receipt.source_account_id for receipt in facts.receipts
    }
    shared = _reserve_account_commitments(
        facts, accounts, sale_limits, available, receipt_sources
    )
    if isinstance(shared, Err):
        return shared
    allocations = check_source_allocations(
        facts, available, shared.value, sale_limits, receipt_sources
    )
    if isinstance(allocations, Err):
        return allocations
    return check_outflows(facts, accounts, sale_limits)


def _sale_limits(
    facts: CaseFacts, accounts: dict[str, Account]
) -> Result[dict[str, Money], PipelineError]:
    sale_limits: dict[str, Money] = {}
    for account_id, account in accounts.items():
        sales = [
            a
            for a in facts.actions
            if a.kind == "dispose"
            and a.source_account_id == account_id
            and a.status in {"agreed", "conditional"}
        ]
        amounts = [
            a.amount or (account.valuation if a.extent == "full" else None)
            for a in sales
        ]
        if amounts and all(
            amount is not None and amount.precision == "exact" for amount in amounts
        ):
            known_amounts = [amount for amount in amounts if amount is not None]
            if len({amount.currency for amount in known_amounts}) > 1:
                return failure(
                    "currency_conflict",
                    "Disposals of the same account mix currencies.",
                    blocked=True,
                )
            sale_limits[account_id] = known_amounts[0].model_copy(
                update={
                    "value": sum((amount.value for amount in known_amounts), Decimal(0))
                }
            )
    return Ok(sale_limits)


def _add_internal_receipts(
    facts: CaseFacts,
    accounts: dict[str, Account],
    sale_limits: dict[str, Money],
    available: dict[str, Money],
) -> Result[None, PipelineError]:
    internal_totals: dict[str, Decimal] = {}
    for receipt in facts.receipts:
        if (
            not receipt.source_account_id
            or receipt.status != "received"
            or receipt.amount is None
            or receipt.amount.precision != "exact"
        ):
            continue
        account = accounts.get(receipt.source_account_id)
        limit = sale_limits.get(receipt.source_account_id)
        if account and "cash" in account.account_type.casefold():
            limit = account.valuation
        if limit is None or limit.precision != "exact":
            continue
        if receipt.amount.currency != limit.currency:
            return failure(
                "currency_conflict",
                "Internal proceeds and their source use incompatible currencies.",
                blocked=True,
            )
        internal_totals[receipt.source_account_id] = (
            internal_totals.get(receipt.source_account_id, Decimal(0))
            + receipt.amount.value
        )
        if internal_totals[receipt.source_account_id] > limit.value:
            return failure(
                "insufficient_funds",
                "Internal receipts exceed the evidenced disposal or source cash balance.",
                blocked=True,
            )
        obligations = [
            c
            for c in facts.commitments
            if c.receipt_id == receipt.receipt_id and not c.already_reflected
        ]
        if any(c.amount is None or c.amount.precision != "exact" for c in obligations):
            continue
        if any(c.amount and c.amount.currency != limit.currency for c in obligations):
            return failure(
                "currency_conflict",
                "Internal proceeds and commitments use incompatible currencies.",
                blocked=True,
            )
        net = receipt.amount.value - sum(
            (c.amount.value for c in obligations if c.amount), Decimal(0)
        )
        if net < 0:
            return failure(
                "insufficient_funds",
                "Commitments exceed the linked internal proceeds.",
                blocked=True,
            )
        # Internal transfers provide allocation capacity, but never new external wealth.
        available[receipt.receipt_id] = receipt.amount.model_copy(update={"value": net})
    return Ok(None)


def _reserve_account_commitments(
    facts: CaseFacts,
    accounts: dict[str, Account],
    sale_limits: dict[str, Money],
    available: dict[str, Money],
    receipt_sources: dict[str, str | None],
) -> Result[dict[str, Money], PipelineError]:
    account_limits: dict[str, Money] = {}
    for account_id, account in accounts.items():
        limit = account.valuation
        if limit is None or limit.precision != "exact":
            limit = sale_limits.get(account_id)
        if limit is None or limit.precision != "exact":
            continue
        obligations = [
            commitment
            for commitment in facts.commitments
            if receipt_sources.get(commitment.receipt_id) == account_id
            and not commitment.already_reflected
        ]
        if any(
            commitment.amount is not None
            and commitment.amount.currency != limit.currency
            for commitment in obligations
        ):
            return failure(
                "currency_conflict",
                "A linked obligation and shared source account use incompatible currencies.",
                blocked=True,
            )
        if any(
            commitment.amount is None or commitment.amount.precision != "exact"
            for commitment in obligations
        ):
            add_review(
                facts,
                "unknown_commitment",
                "Confirm the linked obligation amount before calculating available source account funds.",
                [account_id],
            )
            for receipt_id in list(available):
                if receipt_sources.get(receipt_id) == account_id:
                    del available[receipt_id]
            continue
        reserved = sum(
            (
                commitment.amount.value
                for commitment in obligations
                if commitment.amount is not None
            ),
            Decimal(0),
        )
        if reserved > limit.value:
            return failure(
                "insufficient_funds",
                "Commitments exceed the shared source account capacity.",
                blocked=True,
            )
        account_limits[account_id] = limit.model_copy(
            update={"value": limit.value - reserved}
        )
        if account_id in sale_limits:
            sale = sale_limits[account_id]
            if reserved > sale.value:
                return failure(
                    "insufficient_funds",
                    "Commitments exceed the shared sale proceeds.",
                    blocked=True,
                )
            sale_limits[account_id] = sale.model_copy(
                update={"value": sale.value - reserved}
            )
    return Ok(account_limits)
