"""Validate recorded actions, allowance conditions and unresolved adviser decisions."""

import json
import re
from datetime import date

from agent_pipeline.contracts import Ok, PipelineError, Result
from agent_pipeline.rules.isa_funding import check_isa_disposal_plans
from agent_pipeline.rules.models import Account, Action, CaseFacts, Receipt
from agent_pipeline.rules.review import add_review, failure


def check_actions(
    facts: CaseFacts, account_map: dict[str, Account], receipt_map: dict[str, Receipt]
) -> Result[tuple[dict[str, Account], list[Action]], PipelineError]:
    destination_accounts = account_map | {
        account.account_id: account for account in facts.planned_accounts
    }
    pension_actions: list[Action] = []
    for action in facts.actions:
        if action.kind == "open" and not action.destination_account_ids:
            return failure(
                "missing_action_destination",
                "An account-opening action must identify the planned account in destination_account_ids.",
            )
        source = account_map.get(action.source_account_id or "")
        cited = " ".join(ref.excerpt for ref in action.refs)
        if (
            action.kind == "transfer"
            and source
            and re.search(
                r"\b(?:GIA|general investment account)\b", source.account_type, re.I
            )
            and re.search(r"\b(?:disinvest|sell|selling|disposal)\b", cited, re.I)
        ):
            return failure(
                "disposal_misclassified",
                "A cited GIA disposal cannot be classified as a transfer.",
                blocked=True,
            )
        isa_targets = [
            identifier
            for identifier in action.destination_account_ids
            if identifier in destination_accounts
            and re.search(
                r"\b(?:ISA|JISA|LISA)\b",
                destination_accounts[identifier].account_type,
                re.I,
            )
        ]
        pension_targets = [
            identifier
            for identifier in action.destination_account_ids
            if identifier in destination_accounts
            and re.search(
                r"\b(?:sipp|pension)\b",
                destination_accounts[identifier].account_type,
                re.I,
            )
        ]
        if (
            pension_targets
            and action.kind == "contribute"
            and action.status in {"agreed", "conditional"}
        ):
            add_review(
                facts,
                "pension_capacity",
                "Confirm the available pension allowance and eligible contribution amount for each account before implementation.",
                pension_targets,
            )
            condition = "Pension contributions require confirmed allowance and contribution eligibility"
            if condition not in action.conditions:
                action.conditions.append(condition)
            pension_actions.append(action)
        if (
            isa_targets
            and action.kind in {"contribute", "transfer", "dispose"}
            and action.status in {"agreed", "conditional"}
            and not (
                action.kind == "transfer"
                and source
                and re.search(r"\b(?:ISA|JISA|LISA)\b", source.account_type, re.I)
                and not action.source_funding_ids
                and not any(
                    re.search(
                        r"\b(?:lifetime|junior|JISA|LISA|help\W+to\W+buy)\b",
                        destination_accounts[key].account_type,
                        re.I,
                    )
                    for key in isa_targets
                )
            )
        ):
            add_review(
                facts,
                "isa_capacity",
                "Confirm existing subscriptions and each recipient's remaining ISA allowance for the relevant tax year before implementation.",
                isa_targets,
            )
            condition = "ISA top-ups require confirmation of each recipient's remaining allowance for the relevant tax year"
            if condition not in action.conditions:
                action.conditions.append(condition)
        if set(action.source_funding_ids) - receipt_map.keys():
            return failure(
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
            return failure(
                "contingent_funding",
                "An unconditional action relies on funds not received.",
                blocked=True,
            )
        if (
            action.kind == "dispose"
            and action.extent == "partial"
            and action.amount is None
        ):
            add_review(
                facts,
                "partial_sale_amount",
                "Confirm the partial disposal amount; the full account value is not sale proceeds.",
                [action.source_account_id] if action.source_account_id else [],
            )
    retained = {
        a.source_account_id
        for a in facts.actions
        if a.kind == "retain" and a.status in {"agreed", "conditional"}
    }
    disposed = {
        a.source_account_id
        for a in facts.actions
        if a.kind == "dispose"
        and a.extent == "full"
        and a.status in {"agreed", "conditional"}
    }
    if retained & disposed:
        return failure(
            "contradictory_actions",
            "The same account cannot be retained and fully disposed of.",
            blocked=True,
        )
    return Ok((destination_accounts, pension_actions))


def finish_action_review(
    facts: CaseFacts,
    destination_accounts: dict[str, Account],
    pension_actions: list[Action],
) -> Result[None, PipelineError]:
    for action in pension_actions:
        action.status = "conditional"
    seen_actions: set[str] = set()
    for action in facts.actions:
        # Exclusions may identify different subjects only through their citations.
        ignored = {"action_id"} if action.kind == "exclude" else {"action_id", "refs"}
        key = json.dumps(
            action.model_dump(mode="json", exclude=ignored),
            sort_keys=True,
        )
        if key in seen_actions:
            return failure(
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
            for fee in facts.fees
        ):
            add_review(facts, code, message)
    if any(item.blocking for item in [*facts.review_items, *facts.conflicts]):
        return failure(
            "unresolved_conflict",
            "Material evidence or instructions remain unresolved.",
            blocked=True,
        )
    if facts.effective_date:
        year = facts.effective_date.year - (
            facts.effective_date < date(facts.effective_date.year, 4, 6)
        )
        facts.tax_year = f"{year}/{str(year + 1)[-2:]}"
    else:
        facts.tax_year = None
        timing = " ".join(
            [
                *(n.text for n in facts.narratives),
                *(a.timing or "" for a in facts.actions),
            ]
        )
        if re.search(r"\b(this|new|next|current)\s+(tax\s+)?year\b", timing, re.I):
            add_review(
                facts,
                "ambiguous_date",
                "Confirm the source date for the relative tax-year instruction.",
            )
    check_isa_disposal_plans(facts, destination_accounts)
    return Ok(None)
