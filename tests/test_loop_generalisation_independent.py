"""Synthetic generalisation checks for report scope, funding, and prose inputs."""

import json

import pytest

from agent_pipeline.contracts import Err, EvidenceBlock, EvidenceBundle, Ok
from agent_pipeline.domain import CaseFacts, reconcile
from agent_pipeline.rendering import render_slot
from agent_pipeline.workflow import select_facts


def evidence_bundle(text: str) -> EvidenceBundle:
    database = {
        "holders": {
            "account_holder": {
                "name": "Morgan Vale",
                "accounts": [
                    {
                        "account_id": "cedar-cash-91",
                        "platform": "Northstar Custody",
                        "type": "Cash Account",
                        "owner": "Morgan Vale",
                        "status": "open",
                        "value": 9200,
                        "currency": "GBP",
                        "valuation_date": "2026-06-18",
                    },
                    {
                        "account_id": "pine-isa-44",
                        "platform": "Harbor Investments",
                        "type": "ISA",
                        "owner": "Morgan Vale",
                        "status": "open",
                        "value": 6300,
                        "currency": "GBP",
                        "valuation_date": "2026-06-18",
                    },
                ],
            }
        }
    }
    return EvidenceBundle(
        [
            EvidenceBlock(
                "database",
                "account-register.json",
                "$",
                json.dumps(database),
                "register-hash",
            ),
            EvidenceBlock(
                "meeting", "planning-notes.txt", "paragraph 1", text, "notes-hash"
            ),
        ],
        [],
        [database],
    )


@pytest.mark.parametrize("known_funding", [True, False])
def test_allocation_review_requests_only_unknown_figures_and_names_destination(
    known_funding,
):
    text = "Scope pine-isa-44. Allocate the settlement funds; contribution size is unconfirmed."
    text += (
        " Received £17,500." if known_funding else " Received an unconfirmed amount."
    )
    refs = [{"evidence_id": "meeting", "excerpt": text}]
    facts = CaseFacts(
        requested_account_ids=["pine-isa-44"],
        scope_refs=refs,
        receipts=[
            {
                "receipt_id": "settlement",
                "description": "Settlement",
                "status": "received",
                "amount": {"value": "17500"} if known_funding else None,
                "refs": refs,
            }
        ],
        actions=[
            {
                "action_id": "allocate",
                "kind": "contribute",
                "source_funding_ids": ["settlement"],
                "destination_account_ids": ["pine-isa-44"],
                "allocation_rule": "Contribution size to be confirmed",
                "refs": refs,
            }
        ],
    )
    result = reconcile(facts, evidence_bundle(text))
    assert isinstance(result, Ok), result
    item = next(
        r for r in result.value.review_items if r.code == "allocation_confirmation"
    )
    assert "allocation" in item.message.lower()
    assert ("available funding" in item.message) is not known_funding
    assert item.account_ids == ["pine-isa-44"]


def test_funding_account_can_support_in_scope_action_without_entering_holdings():
    text = (
        "Scope is limited to pine-isa-44. Morgan agreed to contribute exactly "
        "£7,250 from cedar-cash-91 to pine-isa-44."
    )
    refs = [{"evidence_id": "meeting", "excerpt": text}]
    facts = CaseFacts(
        requested_account_ids=["pine-isa-44"],
        scope_refs=refs,
        actions=[
            {
                "action_id": "move-cash-1",
                "kind": "contribute",
                "source_account_id": "cedar-cash-91",
                "destination_account_ids": ["pine-isa-44"],
                "amount": {"value": "7250"},
                "refs": refs,
            }
        ],
    )

    result = reconcile(facts, evidence_bundle(text))

    assert isinstance(result, Ok), result
    report_facts = result.value.model_dump(mode="json")
    holdings = render_slot("holdings", report_facts, {})
    actions = render_slot("actions", report_facts, {})
    assert "pine-isa-44" in holdings
    assert "cedar-cash-91" not in holdings
    assert "cedar-cash-91" in actions and "pine-isa-44" in actions


@pytest.mark.parametrize("identifier", ["", " "])
def test_planned_account_requires_usable_identity_before_rendering(identifier):
    text = "Open a new joint investment account for Morgan and Rowan."
    refs = [{"evidence_id": "meeting", "excerpt": text}]
    facts = CaseFacts(
        requested_account_ids=[identifier],
        scope_refs=refs,
        planned_accounts=[
            {
                "account_id": identifier,
                "account_type": "Investment account",
                "owners": ["Morgan Vale", "Rowan"],
                "refs": refs,
            }
        ],
        actions=[
            {
                "action_id": "opening",
                "kind": "open",
                "destination_account_ids": [identifier],
                "refs": refs,
            }
        ],
    )
    result = reconcile(facts, evidence_bundle(text))
    assert isinstance(result, Err), result
    assert result.error.code == "invalid_account_id"


def test_pension_funding_keeps_capacity_confirmation_separate_from_retention():
    text = "Contribute £2,000 from cedar-cash-91 to pine-isa-44, subject to pension capacity."
    refs = [{"evidence_id": "meeting", "excerpt": text}]
    bundle = evidence_bundle(text)
    database = bundle.databases[0]
    database["holders"]["account_holder"]["accounts"][1]["type"] = "SIPP"
    bundle.blocks[0] = EvidenceBlock(
        "database", "account-register.json", "$", json.dumps(database), "register-hash"
    )
    facts = CaseFacts(
        requested_account_ids=["pine-isa-44"],
        scope_refs=refs,
        actions=[
            {
                "action_id": "pension-funding",
                "kind": "contribute",
                "source_account_id": "cedar-cash-91",
                "destination_account_ids": ["pine-isa-44"],
                "amount": {"value": "2000"},
                "refs": refs,
            }
        ],
    )
    result = reconcile(facts, bundle)
    assert isinstance(result, Ok), result
    controls = [r for r in result.value.review_items if r.code == "pension_capacity"]
    assert len(controls) == 1 and controls[0].account_ids == ["pine-isa-44"]
    assert result.value.actions[0].status == "conditional"

    facts.actions[0].kind = "retain"
    facts.actions[0].source_account_id = "pine-isa-44"
    facts.actions[0].destination_account_ids = []
    facts.actions[0].amount = None
    retain_text = "Retain pine-isa-44."
    for ref in facts.scope_refs + facts.actions[0].refs:
        ref.excerpt = retain_text
    bundle.blocks[1] = EvidenceBlock(
        "meeting", "planning-notes.txt", "paragraph 1", retain_text, "notes-hash"
    )
    retained = reconcile(facts, bundle)
    assert isinstance(retained, Ok), retained
    assert not any(r.code == "pension_capacity" for r in retained.value.review_items)


def test_planned_pension_contribution_also_requires_capacity_confirmation():
    text = "Contribute £2,000 from cedar-cash-91 to a new SIPP for Morgan."
    refs = [{"evidence_id": "meeting", "excerpt": text}]
    facts = CaseFacts(
        requested_account_ids=["local-pension"],
        scope_refs=refs,
        planned_accounts=[
            {"account_id": "local-pension", "account_type": "SIPP", "refs": refs}
        ],
        actions=[
            {
                "action_id": "fund-planned",
                "kind": "contribute",
                "source_account_id": "cedar-cash-91",
                "destination_account_ids": ["local-pension"],
                "amount": {"value": "2000"},
                "refs": refs,
            }
        ],
    )
    result = reconcile(facts, evidence_bundle(text))
    assert isinstance(result, Ok), result
    assert any(
        r.code == "pension_capacity" and r.account_ids == ["local-pension"]
        for r in result.value.review_items
    )


@pytest.mark.parametrize(
    ("status", "amount", "error"),
    [
        ("contingent", {"value": "100000"}, "contingent_funding"),
        ("pending", {"value": "100000"}, "contingent_funding"),
        ("received", None, "unsupported_funding"),
    ],
)
@pytest.mark.parametrize("planned", [False, True])
def test_pension_review_cannot_soften_invalid_agreed_funding(
    status, amount, error, planned
):
    text = "Proposed pension contribution £100,000; verify settlement funding."
    refs = [{"evidence_id": "meeting", "excerpt": text}]
    bundle = evidence_bundle(text)
    database = bundle.databases[0]
    database["holders"]["account_holder"]["accounts"][1]["type"] = "SIPP"
    bundle.blocks[0] = EvidenceBlock(
        "database", "account-register.json", "$", json.dumps(database), "register-hash"
    )
    target = "new-pension" if planned else "pine-isa-44"
    facts = CaseFacts(
        requested_account_ids=[target],
        scope_refs=refs,
        planned_accounts=[{"account_id": target, "account_type": "SIPP", "refs": refs}]
        if planned
        else [],
        receipts=[
            {
                "receipt_id": "settlement",
                "description": "Settlement",
                "status": status,
                "amount": amount,
                "refs": refs,
            }
        ],
        actions=[
            {
                "action_id": "pension-allocation",
                "kind": "contribute",
                "source_funding_ids": ["settlement"],
                "destination_account_ids": [target],
                "amount": {"value": "100000"},
                "status": "agreed",
                "refs": refs,
            }
        ],
    )
    result = reconcile(facts, bundle)
    assert isinstance(result, Err), result
    assert result.error.code == error


def test_shifted_exact_receipt_and_commitment_amounts_reconcile_and_keep_qualifiers():
    text = (
        "Scope is pine-isa-44. Received £83,517.29. Reserve £3,421.17 for fees. "
        "Allocate the remaining £80,096.12 to pine-isa-44."
    )
    refs = [{"evidence_id": "meeting", "excerpt": text}]
    facts = CaseFacts(
        requested_account_ids=["pine-isa-44"],
        scope_refs=refs,
        receipts=[
            {
                "receipt_id": "settlement-q8",
                "description": "Harbor settlement",
                "amount": {"value": "83517.29"},
                "status": "received",
                "refs": refs,
            }
        ],
        commitments=[
            {
                "commitment_id": "fee-reserve-3",
                "receipt_id": "settlement-q8",
                "description": "Fees reserved",
                "amount": {"value": "3421.17"},
                "status": "unpaid",
                "refs": refs,
            }
        ],
        actions=[
            {
                "action_id": "allocate-settlement-2",
                "kind": "contribute",
                "source_funding_ids": ["settlement-q8"],
                "destination_account_ids": ["pine-isa-44"],
                "amount": {"value": "80096.12"},
                "refs": refs,
            }
        ],
    )

    result = reconcile(facts, evidence_bundle(text))

    assert isinstance(result, Ok), result
    assert str(result.value.funding_balances[0].available.value) == "80096.12"

    qualified = facts.model_copy(deep=True)
    assert qualified.receipts[0].amount is not None
    qualified.receipts[0].amount.precision = "approximate"
    qualified.actions[0].status = "conditional"
    qualified_text = text.replace("Received £83,517.29", "Received around £83,517.29")
    for item in [*qualified.receipts, *qualified.commitments, *qualified.actions]:
        for ref in item.refs:
            ref.excerpt = qualified_text
    for ref in qualified.scope_refs:
        ref.excerpt = qualified_text
    qualified_result = reconcile(qualified, evidence_bundle(qualified_text))
    assert isinstance(qualified_result, Ok), qualified_result
    assert not qualified_result.value.funding_balances
    assert any(
        item.code == "qualified_funding" for item in qualified_result.value.review_items
    )


def test_active_narrative_selectors_exclude_raw_evidence_amounts_and_evaluator_data():
    facts = {
        "narratives": [
            {"category": "objective", "text": "Build flexibility over time."},
            {"category": "circumstance", "text": "Morgan recently changed roles."},
        ],
        "actions": [
            {
                "kind": "contribute",
                "rationale": "Use the available ISA allowance.",
                "amount": {"value": "76543.21"},
                "refs": [{"evidence_id": "private-source-id", "excerpt": "£76,543.21"}],
            }
        ],
        "accounts": [
            {
                "account_id": "pine-isa-44",
                "valuation": {"value": "76543.21", "currency": "GBP"},
            }
        ],
        "evidence": [{"text": "PRIVATE RAW SOURCE TEXT"}],
        "evaluation": {"answer_key": "PRIVATE EVALUATOR DATA"},
    }

    background = select_facts("background", facts)
    rationale = select_facts("rationale", facts)

    for selected in (background, rationale):
        serialized = str(selected)
        assert "evidence" not in selected
        assert "evaluation" not in selected
        assert "76543.21" not in serialized
        assert "private-source-id" not in serialized
        assert "PRIVATE RAW SOURCE TEXT" not in serialized
        assert "PRIVATE EVALUATOR DATA" not in serialized
    assert "Build flexibility over time." in str(background)
    assert "Use the available ISA allowance." in str(rationale)


def test_missing_balance_does_not_make_a_known_open_status_unknown():
    from test_domain import bundle

    result = reconcile(CaseFacts(), bundle(value=None))
    assert isinstance(result, Ok), result
    note = next(
        item for item in result.value.review_items if item.code == "missing_balance"
    )
    assert "balance" in note.message.lower()
    assert "status" not in note.message.lower()


def test_narrative_writers_know_scoped_recipients_without_receiving_accounts():
    facts = {
        "requested_account_ids": ["ACCOUNT-A", "ACCOUNT-B", "PLANNED-C"],
        "accounts": [
            {
                "account_id": "ACCOUNT-A",
                "owners": ["Rowan", "Taylor"],
                "valuation": {"value": "76543"},
            },
            {"account_id": "ACCOUNT-B", "owners": ["Taylor"]},
            {"account_id": "OUTSIDE", "owners": ["Unrelated person"]},
        ],
        "planned_accounts": [
            {"account_id": "PLANNED-C", "owners": ["Rowan", "Taylor"]}
        ],
        "narratives": [
            {"category": "circumstance", "text": "Taylor received an inheritance."}
        ],
    }
    for selector in ("background", "rationale"):
        selected = select_facts(selector, facts)
        assert selected["recipient_names"] == ["Rowan", "Taylor"]
        assert "accounts" not in selected
        assert "76543" not in str(selected)
        assert "Unrelated person" not in str(selected)
