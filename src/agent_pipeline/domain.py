"""Source-backed case facts and pure reconciliation; no provider or filesystem I/O."""

import json
import re
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_pipeline.contracts import (
    Err,
    EvidenceBundle,
    ExtractionError,
    Ok,
    PipelineError,
    ReportBlocked,
    Result,
)


class FactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SourceRef(FactModel):
    evidence_id: str
    excerpt: str


class Money(FactModel):
    value: Decimal = Field(ge=0)
    currency: str = "GBP"
    effective_date: date | None = None
    precision: Literal[
        "exact", "approximate", "lower_bound", "upper_bound", "unknown"
    ] = "exact"


class Account(FactModel):
    account_id: str
    platform: str = ""
    account_type: str = ""
    owners: list[str] = Field(default_factory=list)
    status: Literal["open", "closed", "planned", "unknown"] = "open"
    valuation: Money | None = None
    refs: list[SourceRef] = Field(default_factory=list)


class Observation(FactModel):
    account_id: str
    amount: Money
    basis: str = "market_value"
    refs: list[SourceRef] = Field(default_factory=list)


class Action(FactModel):
    action_id: str
    kind: Literal[
        "contribute",
        "dispose",
        "transfer",
        "open",
        "retain",
        "rebalance",
        "confirm",
        "exclude",
    ]
    source_account_id: str | None = None
    destination_account_ids: list[str] = Field(default_factory=list)
    source_funding_ids: list[str] = Field(default_factory=list)
    amount: Money | None = None
    extent: Literal["full", "partial", "unspecified"] = "unspecified"
    allocation_rule: str | None = None
    rationale: str = ""
    timing: str | None = None
    conditions: list[str] = Field(default_factory=list)
    status: Literal["agreed", "conditional", "future", "excluded"] = "agreed"
    refs: list[SourceRef] = Field(default_factory=list)


class Receipt(FactModel):
    receipt_id: str
    description: str
    amount: Money | None = None
    status: Literal["received", "pending", "contingent"]
    source_account_id: str | None = None
    refs: list[SourceRef] = Field(default_factory=list)


class Commitment(FactModel):
    commitment_id: str
    receipt_id: str
    description: str
    amount: Money | None = None
    status: Literal["unpaid", "paid"] = "unpaid"
    already_reflected: bool = False
    refs: list[SourceRef] = Field(default_factory=list)


class NarrativeFact(FactModel):
    category: Literal[
        "circumstance", "objective", "risk", "timing", "sensitivity", "exclusion"
    ]
    text: str
    refs: list[SourceRef] = Field(default_factory=list)


class Fee(FactModel):
    kind: Literal["initial", "platform", "ongoing_advice", "fund", "other"]
    rate_percent: Decimal | None = Field(default=None, ge=0)
    amount: Money | None = None
    basis: str | None = None
    account_ids: list[str] = Field(default_factory=list)
    confirmed: bool = False
    refs: list[SourceRef] = Field(default_factory=list)


class ReviewItem(FactModel):
    code: str
    message: str
    account_ids: list[str] = Field(default_factory=list)
    blocking: bool = False
    refs: list[SourceRef] = Field(default_factory=list)


class FundingBalance(FactModel):
    receipt_id: str
    available: Money
    deducted_commitment_ids: list[str] = Field(default_factory=list)


class CaseFacts(FactModel):
    effective_date: date | None = None
    tax_year: str | None = None
    accounts: list[Account] = Field(default_factory=list)
    requested_account_ids: list[str] = Field(default_factory=list)
    scope_refs: list[SourceRef] = Field(default_factory=list)
    planned_accounts: list[Account] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    receipts: list[Receipt] = Field(default_factory=list)
    commitments: list[Commitment] = Field(default_factory=list)
    narratives: list[NarrativeFact] = Field(default_factory=list)
    fees: list[Fee] = Field(default_factory=list)
    review_items: list[ReviewItem] = Field(default_factory=list)
    conflicts: list[ReviewItem] = Field(default_factory=list)
    funding_balances: list[FundingBalance] = Field(default_factory=list)


def extraction_schema() -> dict[str, Any]:
    """OpenAI's strict schema requires every property, including nullable ones."""
    schema = CaseFacts.model_json_schema()

    def strict(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for child in node.values():
                strict(child)
        elif isinstance(node, list):
            for child in node:
                strict(child)

    strict(schema)
    return schema


def _normal(text: str) -> str:
    return " ".join(text.split())


def _numbers(text: str) -> set[Decimal]:
    return {
        Decimal(number.replace(",", ""))
        * {
            "k": 1000,
            "thousand": 1000,
            "m": 1000000,
            "million": 1000000,
            "bn": 1000000000,
            "billion": 1000000000,
        }.get(suffix.lower(), 1)
        for number, suffix in re.findall(
            r"(?<![\w.,])(\d+(?:,\d{3})*(?:\.\d+)?)(?:\s*(thousand|million|billion|bn|k|m))?(?!\w|[.,]\d)",
            text,
            re.I,
        )
    }


def _dates(text: str) -> set[date]:
    found: set[date] = set()
    for value in re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text):
        try:
            found.add(date.fromisoformat(value))
        except ValueError:
            continue
    months = {
        name.lower(): index
        for index, name in enumerate(
            (
                "January",
                "February",
                "March",
                "April",
                "May",
                "June",
                "July",
                "August",
                "September",
                "October",
                "November",
                "December",
            ),
            1,
        )
    }
    for day, month, year in re.findall(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})\b", text
    ):
        if month.lower() in months:
            try:
                found.add(date(int(year), months[month.lower()], int(day)))
            except ValueError:
                continue
    return found


def _qualified_number(text: str, value: Decimal) -> bool:
    for match in re.finditer(
        r"\b(?:around|approximately|about|a little over|over|up to|at least)\s*(?:GBP|USD|EUR|[£$€])?\s*(\d+(?:,\d{3})*(?:\.\d+)?(?:\s*(?:thousand|million|billion|bn|k|m))?)(?!\w|[.,]\d)",
        text,
        re.I,
    ):
        if value in _numbers(match.group(1)):
            return True
    return False


def _failure(code: str, message: str, *, blocked: bool = False) -> Err[PipelineError]:
    error_type = ReportBlocked if blocked else ExtractionError
    return Err(error_type(code, message, stage="reconcile"))


def _check_provenance(
    facts: CaseFacts, bundle: EvidenceBundle
) -> Result[None, PipelineError]:
    blocks = {block.id: block for block in bundle.blocks}
    if facts.effective_date and not any(
        facts.effective_date in _dates(block.text)
        for block in bundle.blocks
        if block.role == "evidence"
    ):
        return _failure(
            "unsupported_date",
            "The report effective date is not present in the evidence.",
        )
    items: list[FactModel] = [
        *facts.observations,
        *facts.actions,
        *facts.receipts,
        *facts.commitments,
        *facts.narratives,
        *facts.fees,
        *facts.planned_accounts,
    ]
    refs = [
        *facts.scope_refs,
        *(ref for item in items for ref in getattr(item, "refs", [])),
        *(ref for item in [*facts.review_items, *facts.conflicts] for ref in item.refs),
    ]
    for ref in refs:
        block = blocks.get(ref.evidence_id)
        if (
            not block
            or block.role != "evidence"
            or not ref.excerpt.strip()
            or _normal(ref.excerpt) not in _normal(block.text)
        ):
            return _failure(
                "unsupported_reference",
                "A fact has an invalid evidence reference or excerpt.",
            )
    for item in items:
        item_refs: list[SourceRef] = getattr(item, "refs", [])
        if not item_refs:
            return _failure(
                "missing_provenance", "A material fact has no supporting evidence."
            )
        amounts = [getattr(item, "amount", None)]
        if isinstance(item, Account):
            amounts.append(item.valuation)
        cited_text = " ".join(ref.excerpt for ref in item_refs)
        supported = _numbers(cited_text)
        source_names = {blocks[ref.evidence_id].source for ref in item_refs}
        source_dates = set().union(
            *(
                _dates(block.text)
                for block in bundle.blocks
                if block.source in source_names and block.role == "evidence"
            )
        )
        for amount in amounts:
            if amount is not None and amount.value not in supported:
                return _failure(
                    "unsupported_amount",
                    "An amount is not supported by its cited evidence.",
                )
            if (
                amount is not None
                and amount.effective_date
                and amount.effective_date not in source_dates
            ):
                return _failure(
                    "unsupported_date",
                    "An amount's effective date is not supported by its source.",
                )
            if (
                amount is not None
                and amount.precision == "exact"
                and _qualified_number(cited_text, amount.value)
            ):
                return _failure(
                    "unsupported_precision",
                    "Qualified evidence cannot support an exact amount.",
                )
        if isinstance(item, Fee) and item.rate_percent is not None:
            rates = {
                Decimal(value)
                for value in re.findall(
                    r"(?<![\d.])(\d+(?:\.\d+)?)\s*(?:%|per cent|percent)",
                    cited_text,
                    re.I,
                )
            }
            if item.rate_percent not in rates:
                return _failure(
                    "unsupported_amount",
                    "A fee rate is not supported by its cited evidence.",
                )
    return Ok(None)


def _database_accounts(
    bundle: EvidenceBundle,
) -> Result[tuple[list[Account], list[Observation]], PipelineError]:
    accounts: dict[str, Account] = {}
    observations: list[Observation] = []
    try:
        for database in bundle.databases:
            db_block = None
            for block in bundle.blocks:
                if block.role == "evidence" and block.locator == "$":
                    try:
                        # Preserve decimal source literals while accepting older float callers.
                        for parsed in (
                            json.loads(block.text, parse_float=Decimal),
                            json.loads(block.text),
                        ):
                            if (
                                parsed == database
                                or isinstance(parsed, list)
                                and database in parsed
                            ):
                                db_block = block
                                break
                        if db_block:
                            break
                    except (ValueError, TypeError):
                        continue
            refs = (
                [SourceRef(evidence_id=db_block.id, excerpt=db_block.text)]
                if db_block
                else []
            )
            holders = database.get("holders", {})
            for holder in holders.values():
                for raw in holder.get("accounts", []):
                    account_id = raw["account_id"]
                    owner = (
                        holder["name"]
                        if raw.get("owner") == "Joint"
                        else raw.get("owner", holder["name"])
                    )
                    amount = (
                        None
                        if raw.get("value") is None
                        else Money(
                            value=Decimal(str(raw["value"])),
                            currency=raw.get("currency", "GBP"),
                            effective_date=raw.get("valuation_date"),
                        )
                    )
                    account = Account(
                        account_id=account_id,
                        platform=raw.get("platform", ""),
                        account_type=raw.get("type", ""),
                        owners=[owner],
                        status=raw.get("status", "unknown"),
                        refs=refs,
                    )
                    if account_id in accounts:
                        existing = accounts[account_id]
                        if (
                            existing.platform,
                            existing.account_type,
                            existing.status,
                        ) != (account.platform, account.account_type, account.status):
                            return _failure(
                                "account_identity_conflict",
                                "Database account identities conflict.",
                                blocked=True,
                            )
                        existing.owners = sorted(set(existing.owners + account.owners))
                    else:
                        accounts[account_id] = account
                    if amount:
                        observation = Observation(
                            account_id=account_id, amount=amount, refs=refs
                        )
                        if observation not in observations:
                            observations.append(observation)
    except (KeyError, TypeError, AttributeError, ValidationError, ValueError):
        return _failure("invalid_database", "Account database structure is invalid.")
    return Ok((list(accounts.values()), observations))


def _review(
    facts: CaseFacts, code: str, message: str, account_ids: list[str] | None = None
) -> None:
    item = ReviewItem(code=code, message=message, account_ids=account_ids or [])
    if item not in facts.review_items:
        facts.review_items.append(item)


def _check_allocations(
    facts: CaseFacts, accounts: dict[str, Account]
) -> Result[None, PipelineError]:
    available = {
        balance.receipt_id: balance.available for balance in facts.funding_balances
    }
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
                return _failure(
                    "currency_conflict",
                    "Disposals of the same account mix currencies.",
                    blocked=True,
                )
            sale_limits[account_id] = known_amounts[0].model_copy(
                update={
                    "value": sum((amount.value for amount in known_amounts), Decimal(0))
                }
            )
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
            return _failure(
                "currency_conflict",
                "Internal proceeds and their source use incompatible currencies.",
                blocked=True,
            )
        internal_totals[receipt.source_account_id] = (
            internal_totals.get(receipt.source_account_id, Decimal(0))
            + receipt.amount.value
        )
        if internal_totals[receipt.source_account_id] > limit.value:
            return _failure(
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
            return _failure(
                "currency_conflict",
                "Internal proceeds and commitments use incompatible currencies.",
                blocked=True,
            )
        net = receipt.amount.value - sum(
            (c.amount.value for c in obligations if c.amount), Decimal(0)
        )
        if net < 0:
            return _failure(
                "insufficient_funds",
                "Commitments exceed the linked internal proceeds.",
                blocked=True,
            )
        # Internal transfers provide allocation capacity, but never new external wealth.
        available[receipt.receipt_id] = receipt.amount.model_copy(update={"value": net})
    receipt_sources = {
        receipt.receipt_id: receipt.source_account_id for receipt in facts.receipts
    }
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
            return _failure(
                "currency_conflict",
                "A linked obligation and shared source account use incompatible currencies.",
                blocked=True,
            )
        if any(
            commitment.amount is None or commitment.amount.precision != "exact"
            for commitment in obligations
        ):
            _review(
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
            return _failure(
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
                return _failure(
                    "insufficient_funds",
                    "Commitments exceed the shared sale proceeds.",
                    blocked=True,
                )
            sale_limits[account_id] = sale.model_copy(
                update={"value": sale.value - reserved}
            )
    source_money = {"receipt:" + key: money for key, money in available.items()}
    source_money.update(
        {"account:" + key: money for key, money in account_limits.items()}
    )
    source_money.update(
        {
            "sale:" + key: money
            for key, money in sale_limits.items()
            if key in account_limits
        }
    )

    def action_sources(action: Action) -> list[str]:
        if action.source_funding_ids:
            return ["receipt:" + key for key in set(action.source_funding_ids)]
        if action.source_account_id:
            prefix = (
                "sale:"
                if action.kind == "contribute"
                and action.source_account_id in sale_limits
                else "account:"
            )
            return [prefix + action.source_account_id]
        return []

    allocations = [
        a
        for a in facts.actions
        if a.status in {"agreed", "conditional"}
        and a.kind in {"contribute", "transfer"}
        and (a.source_funding_ids or a.source_account_id)
    ]
    checked: list[Action] = []
    for action in allocations:
        sources = action_sources(action)
        funding = [source_money[key] for key in sources if key in source_money]
        missing = len(funding) != len(sources)
        if missing or action.amount is None or action.amount.precision != "exact":
            if missing and action.status == "agreed" and action.amount is not None:
                return _failure(
                    "unsupported_funding",
                    "An exact unconditional allocation relies on unconfirmed available funds.",
                    blocked=True,
                )
            _review(
                facts,
                "allocation_confirmation",
                "Confirm exact available funding and allocations before implementation.",
            )
            continue
        if any(amount.currency != action.amount.currency for amount in funding):
            return _failure(
                "currency_conflict",
                "An allocation and its funding use incompatible currencies.",
                blocked=True,
            )
        checked.append(action)

    # All routes from an account share one upstream capacity. Disposals create
    # a proceeds pool rather than a second expense; internal receipts draw from
    # that pool, so direct reinvestment and receipt-backed reinvestment compete
    # for the same money. Decimal residual capacities preserve pennies.
    residual: dict[str, dict[str, Decimal]] = {}

    def edge(left: str, right: str, capacity: Decimal) -> None:
        residual.setdefault(left, {})[right] = capacity
        residual.setdefault(right, {}).setdefault(left, Decimal(0))

    for account_id, money in account_limits.items():
        edge("start", "account:" + account_id, money.value)
    for account_id, money in sale_limits.items():
        if account_id in account_limits:
            edge("account:" + account_id, "sale:" + account_id, money.value)
    for receipt_id, money in available.items():
        account_id = receipt_sources.get(receipt_id)
        parent = "start"
        if account_id:
            parent = ("sale:" if account_id in sale_limits else "account:") + account_id
        edge(parent, "receipt:" + receipt_id, money.value)
    required = Decimal(0)
    for index, action in enumerate(checked):
        if action.amount is None:
            continue
        node = "action:" + str(index)
        edge(node, "end", action.amount.value)
        required += action.amount.value
        for source in action_sources(action):
            edge(source, node, action.amount.value)
    routed = Decimal(0)
    while routed < required:
        parents: dict[str, str] = {"start": ""}
        queue = ["start"]
        for node in queue:
            for neighbor, capacity in residual.get(node, {}).items():
                if capacity > 0 and neighbor not in parents:
                    parents[neighbor] = node
                    queue.append(neighbor)
            if "end" in parents:
                break
        if "end" not in parents:
            return _failure(
                "insufficient_funds",
                "The authorised funding sources cannot support all allocations.",
                blocked=True,
            )
        increment = required - routed
        node = "end"
        while node != "start":
            parent = parents[node]
            increment = min(increment, residual[parent][node])
            node = parent
        node = "end"
        while node != "start":
            parent = parents[node]
            residual[parent][node] -= increment
            residual[node][parent] += increment
            node = parent
        routed += increment

    for account_id, account in accounts.items():
        outflows = [
            a
            for a in facts.actions
            if a.source_account_id == account_id
            and a.status in {"agreed", "conditional"}
            and a.kind in {"dispose", "transfer", "contribute"}
            and a.amount
        ]
        if not outflows:
            continue
        reinvestments = [
            a for a in outflows if a.kind == "contribute" and account_id in sale_limits
        ]
        if reinvestments:
            sale_limit = sale_limits[account_id]
            if any(
                a.amount and a.amount.currency != sale_limit.currency
                for a in reinvestments
            ):
                return _failure(
                    "currency_conflict",
                    "Reinvestment and sale proceeds use incompatible currencies.",
                    blocked=True,
                )
            if (
                sum((a.amount.value for a in reinvestments if a.amount), Decimal(0))
                > sale_limit.value
            ):
                return _failure(
                    "insufficient_funds",
                    "Reinvestments exceed the evidenced sale proceeds.",
                    blocked=True,
                )
            outflows = [a for a in outflows if a not in reinvestments]
        if account.valuation is None or account.valuation.precision != "exact":
            _review(
                facts,
                "source_balance",
                "Confirm the available source account balance before implementation.",
                [account_id],
            )
            continue
        if any(
            a.amount and a.amount.currency != account.valuation.currency
            for a in outflows
        ):
            return _failure(
                "currency_conflict",
                "An outgoing action and its source account use incompatible currencies.",
                blocked=True,
            )
        if (
            sum((a.amount.value for a in outflows if a.amount), Decimal(0))
            > account.valuation.value
        ):
            return _failure(
                "insufficient_funds",
                "Outgoing actions exceed the known source account balance.",
                blocked=True,
            )
    return Ok(None)


def reconcile(
    facts: CaseFacts, bundle: EvidenceBundle
) -> Result[CaseFacts, PipelineError]:
    """Validate support, select dated valuations, and calculate only exact receipt funds."""
    support = _check_provenance(facts, bundle)
    if isinstance(support, Err):
        return support
    database = _database_accounts(bundle)
    if isinstance(database, Err):
        return database
    result = facts.model_copy(deep=True)
    result.accounts, db_observations = database.value
    result.funding_balances = []
    account_map = {account.account_id: account for account in result.accounts}
    planned_ids = {account.account_id for account in result.planned_accounts}
    if planned_ids & account_map.keys() or len(planned_ids) != len(
        result.planned_accounts
    ):
        return _failure(
            "account_identity_conflict",
            "Planned and existing account identities overlap.",
            blocked=True,
        )
    for account in result.planned_accounts:
        account.status = "planned"
        if account.valuation is not None:
            return _failure(
                "planned_valuation", "A planned account cannot have existing holdings."
            )
    known_ids = account_map.keys() | planned_ids
    referenced_ids = set(result.requested_account_ids)
    for action in result.actions:
        referenced_ids.update(action.destination_account_ids)
        if action.source_account_id:
            referenced_ids.add(action.source_account_id)
    referenced_ids.update(observation.account_id for observation in result.observations)
    referenced_ids.update(
        receipt.source_account_id
        for receipt in result.receipts
        if receipt.source_account_id
    )
    referenced_ids.update(
        account_id for fee in result.fees for account_id in fee.account_ids
    )
    if referenced_ids - known_ids:
        return _failure(
            "unknown_account",
            "A scoped or action account cannot be resolved to the database or an evidenced planned account.",
            blocked=True,
        )
    if result.requested_account_ids and not result.scope_refs:
        return _failure(
            "missing_scope_provenance",
            "Report scope needs supporting request evidence.",
        )
    result.requested_account_ids = list(dict.fromkeys(result.requested_account_ids))
    if result.requested_account_ids:
        for action in result.actions:
            if action.status not in {"agreed", "conditional"}:
                continue
            advised_ids = set(action.destination_account_ids)
            if (
                action.kind in {"dispose", "retain", "rebalance"}
                and action.source_account_id
            ):
                advised_ids.add(action.source_account_id)
            if advised_ids - set(result.requested_account_ids):
                return _failure(
                    "out_of_scope_action",
                    "An action advises on an account outside the requested scope.",
                    blocked=True,
                )
    observations = db_observations + [
        x for x in result.observations if x not in db_observations
    ]
    result.observations = observations
    for account in result.accounts:
        values = [
            x
            for x in observations
            if x.account_id == account.account_id and x.basis == "market_value"
        ]
        if not values:
            if account.status != "closed":
                _review(
                    result,
                    "missing_balance",
                    "Confirm the outstanding account balance and status before finalising.",
                    [account.account_id],
                )
            continue
        currencies = {x.amount.currency for x in values}
        if len(currencies) != 1:
            return _failure(
                "currency_conflict",
                "Account valuations use incompatible currencies.",
                blocked=True,
            )
        dated = [x for x in values if x.amount.effective_date is not None]
        if len(dated) != len(values):
            _review(
                result,
                "undated_valuation",
                "An undated valuation cannot establish recency; confirm its date.",
                [account.account_id],
            )
        latest_date = max(
            (x.amount.effective_date for x in dated if x.amount.effective_date),
            default=None,
        )
        candidates = [
            x for x in (dated or values) if x.amount.effective_date == latest_date
        ]
        if len({(x.amount.value, x.amount.precision) for x in candidates}) > 1:
            if account.account_id in result.requested_account_ids:
                return _failure(
                    "valuation_conflict",
                    "Conflicting same-date valuations need resolution.",
                    blocked=True,
                )
            _review(
                result,
                "valuation_conflict",
                "Conflicting same-date valuations need resolution.",
                [account.account_id],
            )
            continue
        account.valuation = candidates[0].amount.model_copy(deep=True)
    receipt_map = {receipt.receipt_id: receipt for receipt in result.receipts}
    if len(receipt_map) != len(result.receipts):
        return _failure("duplicate_receipt", "Receipt identities must be unique.")
    commitment_map = {item.commitment_id: item for item in result.commitments}
    if len(commitment_map) != len(result.commitments):
        return _failure("duplicate_commitment", "Commitment identities must be unique.")
    for events, identity in [
        (result.receipts, "receipt_id"),
        (result.commitments, "commitment_id"),
    ]:
        seen: set[str] = set()
        for event in events:
            signature = event.model_dump(mode="json", exclude={identity, "refs"})
            signature["description"] = _normal(signature["description"]).casefold()
            key = json.dumps(signature, sort_keys=True)
            if key in seen:
                return _failure(
                    "duplicate_economic_event",
                    "Matching financial events need deduplication before funds can be calculated.",
                    blocked=True,
                )
            seen.add(key)
    if any(item.receipt_id not in receipt_map for item in result.commitments):
        return _failure(
            "unknown_receipt", "A commitment has no matching receipt.", blocked=True
        )
    for receipt in result.receipts:
        if receipt.status != "received" or receipt.source_account_id:
            continue
        if receipt.amount is None or receipt.amount.precision != "exact":
            _review(
                result,
                "qualified_funding",
                "Confirm the exact received amount before allocating funds.",
            )
            continue
        commitments = [
            x
            for x in result.commitments
            if x.receipt_id == receipt.receipt_id and not x.already_reflected
        ]
        if any(x.amount is None or x.amount.precision != "exact" for x in commitments):
            _review(
                result,
                "unknown_commitment",
                "Confirm the commitment amount before calculating available funds.",
            )
            continue
        if any(
            x.amount and x.amount.currency != receipt.amount.currency
            for x in commitments
        ):
            return _failure(
                "currency_conflict",
                "A receipt and commitment have incompatible currencies.",
                blocked=True,
            )
        available = receipt.amount.value - sum(
            (x.amount.value for x in commitments if x.amount), Decimal(0)
        )
        if available < 0:
            return _failure(
                "insufficient_funds",
                "Commitments exceed the linked received funds.",
                blocked=True,
            )
        result.funding_balances.append(
            FundingBalance(
                receipt_id=receipt.receipt_id,
                available=receipt.amount.model_copy(update={"value": available}),
                deducted_commitment_ids=[x.commitment_id for x in commitments],
            )
        )
    for action in result.actions:
        if set(action.source_funding_ids) - receipt_map.keys():
            return _failure(
                "unknown_receipt",
                "An action refers to unsupported funding.",
                blocked=True,
            )
        if (
            any(
                receipt_map[key].status != "received"
                for key in action.source_funding_ids
            )
            and action.status == "agreed"
        ):
            return _failure(
                "contingent_funding",
                "An unconditional action relies on funds not received.",
                blocked=True,
            )
        if (
            action.kind == "dispose"
            and action.extent == "partial"
            and action.amount is None
        ):
            _review(
                result,
                "partial_sale_amount",
                "Confirm the partial disposal amount; the full account value is not sale proceeds.",
                [action.source_account_id] if action.source_account_id else [],
            )
    retained = {
        a.source_account_id
        for a in result.actions
        if a.kind == "retain" and a.status in {"agreed", "conditional"}
    }
    disposed = {
        a.source_account_id
        for a in result.actions
        if a.kind == "dispose"
        and a.extent == "full"
        and a.status in {"agreed", "conditional"}
    }
    if retained & disposed:
        return _failure(
            "contradictory_actions",
            "The same account cannot be retained and fully disposed of.",
            blocked=True,
        )
    funding_check = _check_allocations(result, account_map)
    if isinstance(funding_check, Err):
        return funding_check
    seen_actions: set[str] = set()
    for action in result.actions:
        key = json.dumps(
            action.model_dump(mode="json", exclude={"action_id", "refs"}),
            sort_keys=True,
        )
        if key in seen_actions:
            return _failure(
                "duplicate_action",
                "Matching actions must be deduplicated before implementation.",
                blocked=True,
            )
        seen_actions.add(key)
    for kind, code, message in [
        (
            "platform",
            "platform_fees",
            "Confirm relevant platform charges before finalising.",
        ),
        (
            "ongoing_advice",
            "ongoing_advice_fees",
            "Confirm the ongoing advice charge before finalising.",
        ),
    ]:
        if not any(
            fee.kind == kind
            and fee.confirmed
            and (fee.rate_percent is not None or fee.amount is not None)
            for fee in result.fees
        ):
            _review(result, code, message)
    if any(item.blocking for item in [*result.review_items, *result.conflicts]):
        return _failure(
            "unresolved_conflict",
            "Material evidence or instructions remain unresolved.",
            blocked=True,
        )
    if result.effective_date:
        year = result.effective_date.year - (
            result.effective_date < date(result.effective_date.year, 4, 6)
        )
        result.tax_year = f"{year}/{str(year + 1)[-2:]}"
    else:
        result.tax_year = None
        timing = " ".join(
            [
                *(n.text for n in result.narratives),
                *(a.timing or "" for a in result.actions),
            ]
        )
        if re.search(r"\b(this|new|next|current)\s+(tax\s+)?year\b", timing, re.I):
            _review(
                result,
                "ambiguous_date",
                "Confirm the source date for the relative tax-year instruction.",
            )
    return Ok(result)
