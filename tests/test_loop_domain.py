import json

from agent_pipeline.contracts import Err, EvidenceBlock, EvidenceBundle, Ok
from agent_pipeline.domain import CaseFacts, reconcile


def evidence(text):
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


def ref(text):
    return [{"evidence_id": "meeting", "excerpt": text}]


def test_explicit_gia_disposal_cannot_be_a_transfer():
    text = (
        "We agreed to disinvest the GIA in full and transfer the proceeds into the ISA."
    )
    facts = CaseFacts(
        actions=[
            {
                "action_id": "sale",
                "kind": "transfer",
                "source_account_id": "GIA",
                "destination_account_ids": ["ISA"],
                "refs": ref(text),
            }
        ]
    )
    result = reconcile(facts, evidence(text))
    assert isinstance(result, Err)
    assert result.error.code == "disposal_misclassified"


def test_already_funded_isa_requires_remaining_capacity_and_excess_review():
    text = "We agreed to disinvest the GIA in full for the ISA. The ISA is already part-funded; use its remaining allowance."
    facts = CaseFacts(
        actions=[
            {
                "action_id": "sale",
                "kind": "dispose",
                "source_account_id": "GIA",
                "destination_account_ids": ["ISA"],
                "extent": "full",
                "refs": ref(text),
            }
        ]
    )
    result = reconcile(facts, evidence(text))
    assert isinstance(result, Ok)
    review = next(
        item for item in result.value.review_items if item.code == "isa_capacity"
    )
    assert "remaining" in review.message.lower() and "excess" in review.message.lower()


def test_pooled_account_proceeds_and_external_receipt_can_fund_one_allocation():
    text = "Sell the GIA in full for £30,000. Alex received inheritance of £100,000. Together invest £130,000."
    facts = CaseFacts(
        actions=[
            {
                "action_id": "sale",
                "kind": "dispose",
                "source_account_id": "GIA",
                "extent": "full",
                "amount": {"value": "30000"},
                "refs": ref(text),
            },
            {
                "action_id": "pool",
                "kind": "contribute",
                "source_account_id": "GIA",
                "source_funding_ids": ["inheritance"],
                "amount": {"value": "130000"},
                "refs": ref(text),
            },
        ],
        receipts=[
            {
                "receipt_id": "inheritance",
                "description": "Alex's inheritance",
                "amount": {"value": "100000"},
                "status": "received",
                "refs": ref(text),
            }
        ],
    )
    assert isinstance(reconcile(facts, evidence(text)), Ok)


def test_observation_basis_cannot_silently_hide_a_new_valuation():
    import pytest
    from pydantic import ValidationError

    from agent_pipeline.domain import Observation

    with pytest.raises(ValidationError):
        Observation.model_validate(
            {
                "account_id": "GIA",
                "amount": {"value": "45000"},
                "basis": "Live meeting valuation",
            }
        )


def test_open_action_requires_a_destination():
    result = reconcile(
        CaseFacts(
            actions=[
                {
                    "action_id": "open",
                    "kind": "open",
                    "refs": ref("Open a new joint account"),
                }
            ]
        ),
        evidence("Open a new joint account"),
    )
    assert isinstance(result, Err)
    assert result.error.code == "missing_action_destination"
