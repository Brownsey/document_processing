"""Independent negative checks for pooled funding safety."""

import json

from agent_pipeline.contracts import Err, EvidenceBlock, EvidenceBundle
from agent_pipeline.domain import CaseFacts, reconcile


def bundle(text: str) -> EvidenceBundle:
    database = {
        "holders": {
            "client": {
                "name": "Alex",
                "accounts": [
                    {
                        "account_id": "GIA",
                        "platform": "Platform",
                        "type": "General Investment Account",
                        "owner": "Alex",
                        "status": "open",
                        "value": 30000,
                        "currency": "GBP",
                        "valuation_date": "2026-04-30",
                    },
                    {
                        "account_id": "ISA",
                        "platform": "Platform",
                        "type": "ISA",
                        "owner": "Alex",
                        "status": "open",
                        "value": 10000,
                        "currency": "GBP",
                        "valuation_date": "2026-04-30",
                    },
                ],
            }
        }
    }
    return EvidenceBundle(
        [
            EvidenceBlock("db", "accounts.json", "$", json.dumps(database), "dbhash"),
            EvidenceBlock("meeting", "notes.docx", "paragraph 1", text, "meetinghash"),
        ],
        [],
        [database],
    )


def test_pooled_sources_cannot_be_allocated_twice():
    text = (
        "Scope ISA. Sell the GIA in full for £30,000. Alex received inheritance of £100,000. "
        "Together invest £130,000 in the ISA, then invest a further £1 in the ISA."
    )
    source = [{"evidence_id": "meeting", "excerpt": text}]
    facts = CaseFacts(
        requested_account_ids=["GIA", "ISA"],
        scope_refs=source,
        actions=[
            {
                "action_id": "sale",
                "kind": "dispose",
                "source_account_id": "GIA",
                "extent": "full",
                "amount": {"value": "30000"},
                "refs": source,
            },
            {
                "action_id": "pool",
                "kind": "contribute",
                "source_account_id": "GIA",
                "source_funding_ids": ["inheritance"],
                "destination_account_ids": ["ISA"],
                "amount": {"value": "130000"},
                "refs": source,
            },
            {
                "action_id": "extra",
                "kind": "contribute",
                "source_account_id": "GIA",
                "destination_account_ids": ["ISA"],
                "amount": {"value": "1"},
                "refs": source,
            },
        ],
        receipts=[
            {
                "receipt_id": "inheritance",
                "description": "Alex's inheritance",
                "amount": {"value": "100000"},
                "status": "received",
                "refs": source,
            }
        ],
    )

    result = reconcile(facts, bundle(text))

    assert isinstance(result, Err)
    assert result.error.code == "insufficient_funds"


def test_agreed_allocation_cannot_rely_on_contingent_receipt():
    text = (
        "Scope ISA. The £100,000 earnout is contingent. Allocate £100,000 to the ISA."
    )
    source = [{"evidence_id": "meeting", "excerpt": text}]
    facts = CaseFacts(
        requested_account_ids=["GIA", "ISA"],
        scope_refs=source,
        actions=[
            {
                "action_id": "allocation",
                "kind": "contribute",
                "source_funding_ids": ["earnout"],
                "destination_account_ids": ["ISA"],
                "amount": {"value": "100000"},
                "status": "agreed",
                "refs": source,
            }
        ],
        receipts=[
            {
                "receipt_id": "earnout",
                "description": "Earnout",
                "amount": {"value": "100000"},
                "status": "contingent",
                "refs": source,
            }
        ],
    )

    result = reconcile(facts, bundle(text))

    assert isinstance(result, Err)
    assert result.error.code == "contingent_funding"


def test_narrative_selectors_redact_unmarked_numeric_financial_amounts():
    from agent_pipeline.workflow import select_facts

    facts = {
        "narratives": [
            {
                "category": "objective",
                "text": "Invest the inheritance of 120000 and budget 5000 monthly",
            },
            {"category": "sensitivity", "text": "Client asked about charges on 50000"},
        ],
        "actions": [
            {"action_id": "a1", "rationale": "Invest 120000 for future income"}
        ],
    }

    background = select_facts("background", facts)
    rationale = select_facts("rationale", facts)

    assert "120000" not in str(background)
    assert "5000" not in str(background)
    assert "50000" not in str(rationale)
    assert "120000" not in str(rationale)
