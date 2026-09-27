"""Reconcile database identities, requested scope and dated account valuations."""

import json
from decimal import Decimal

from pydantic import ValidationError

from agent_pipeline.contracts import EvidenceBundle, Ok, PipelineError, Result
from agent_pipeline.rules.models import (
    Account,
    CaseFacts,
    Money,
    Observation,
    SourceRef,
)
from agent_pipeline.rules.review import add_review, failure


def database_accounts(
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
                            return failure(
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
        return failure("invalid_database", "Account database structure is invalid.")
    return Ok((list(accounts.values()), observations))


def check_account_scope(facts: CaseFacts) -> Result[dict[str, Account], PipelineError]:
    account_map = {account.account_id: account for account in facts.accounts}
    planned_ids = {account.account_id for account in facts.planned_accounts}
    if planned_ids & account_map.keys() or len(planned_ids) != len(
        facts.planned_accounts
    ):
        return failure(
            "account_identity_conflict",
            "Planned and existing account identities overlap.",
            blocked=True,
        )
    for account in facts.planned_accounts:
        if not account.account_id.strip():
            return failure(
                "invalid_account_id",
                "Assign each planned account a distinct nonblank local identifier and reuse it in scope and actions.",
            )
        account.status = "planned"
        if account.valuation is not None:
            return failure(
                "planned_valuation", "A planned account cannot have existing holdings."
            )
    known_ids = account_map.keys() | planned_ids
    referenced_ids = set(facts.requested_account_ids)
    for action in facts.actions:
        referenced_ids.update(action.destination_account_ids)
        if action.source_account_id:
            referenced_ids.add(action.source_account_id)
    referenced_ids.update(observation.account_id for observation in facts.observations)
    referenced_ids.update(
        receipt.source_account_id
        for receipt in facts.receipts
        if receipt.source_account_id
    )
    referenced_ids.update(
        account_id for fee in facts.fees for account_id in fee.account_ids
    )
    if referenced_ids - known_ids:
        return failure(
            "unknown_account",
            "A scoped or action account cannot be resolved to the database or an evidenced planned account.",
            blocked=True,
        )
    if facts.requested_account_ids and not facts.scope_refs:
        return failure(
            "missing_scope_provenance",
            "Report scope needs supporting request evidence.",
        )
    facts.requested_account_ids = list(dict.fromkeys(facts.requested_account_ids))
    if facts.requested_account_ids:
        for action in facts.actions:
            if action.status not in {"agreed", "conditional"}:
                continue
            advised_ids = set(action.destination_account_ids)
            if (
                action.kind in {"dispose", "retain", "rebalance"}
                and action.source_account_id
            ):
                advised_ids.add(action.source_account_id)
            if advised_ids - set(facts.requested_account_ids):
                return failure(
                    "out_of_scope_action",
                    "An action advises on an account outside the requested scope.",
                    blocked=True,
                )
    return Ok(account_map)


def select_valuations(
    facts: CaseFacts, db_observations: list[Observation]
) -> Result[None, PipelineError]:
    observations = db_observations + [
        x for x in facts.observations if x not in db_observations
    ]
    facts.observations = observations
    for account in facts.accounts:
        values = [
            x
            for x in observations
            if x.account_id == account.account_id and x.basis == "market_value"
        ]
        if not values:
            if account.status != "closed":
                add_review(
                    facts,
                    "missing_balance",
                    "Confirm the outstanding account balance"
                    + (" and status" if account.status == "unknown" else "")
                    + " before finalising.",
                    [account.account_id],
                )
            continue
        currencies = {x.amount.currency for x in values}
        if len(currencies) != 1:
            return failure(
                "currency_conflict",
                "Account valuations use incompatible currencies.",
                blocked=True,
            )
        dated = [x for x in values if x.amount.effective_date is not None]
        if len(dated) != len(values):
            add_review(
                facts,
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
            if account.account_id in facts.requested_account_ids:
                return failure(
                    "valuation_conflict",
                    "Conflicting same-date valuations need resolution.",
                    blocked=True,
                )
            add_review(
                facts,
                "valuation_conflict",
                "Conflicting same-date valuations need resolution.",
                [account.account_id],
            )
            continue
        account.valuation = candidates[0].amount.model_copy(deep=True)
    return Ok(None)
