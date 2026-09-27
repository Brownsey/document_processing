"""Reconcile supported facts in order: accounts, funding, actions and review controls."""

from agent_pipeline.contracts import Err, EvidenceBundle, Ok, PipelineError, Result
from agent_pipeline.rules.accounts import (
    check_account_scope,
    database_accounts,
    select_valuations,
)
from agent_pipeline.rules.actions import check_actions, finish_action_review
from agent_pipeline.rules.funding import check_allocations, prepare_funding
from agent_pipeline.rules.models import CaseFacts
from agent_pipeline.rules.provenance import check_provenance


def reconcile(
    facts: CaseFacts, bundle: EvidenceBundle
) -> Result[CaseFacts, PipelineError]:
    """Apply each rule stage to a copy; return the first failure without changing input."""
    support = check_provenance(facts, bundle)
    if isinstance(support, Err):
        return support
    database = database_accounts(bundle)
    if isinstance(database, Err):
        return database
    result = facts.model_copy(deep=True)
    result.accounts, observations = database.value
    result.funding_balances = []
    accounts = check_account_scope(result)
    if isinstance(accounts, Err):
        return accounts
    valuations = select_valuations(result, observations)
    if isinstance(valuations, Err):
        return valuations
    receipts = prepare_funding(result)
    if isinstance(receipts, Err):
        return receipts
    actions = check_actions(result, accounts.value, receipts.value)
    if isinstance(actions, Err):
        return actions
    funding = check_allocations(result, accounts.value)
    if isinstance(funding, Err):
        return funding
    destinations, pensions = actions.value
    reviewed = finish_action_review(result, destinations, pensions)
    if isinstance(reviewed, Err):
        return reviewed
    return Ok(result)
