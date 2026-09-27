"""Independent checks for funding and control visibility in the split layout."""

from copy import deepcopy

from agent_pipeline.rendering import render_slot


def test_pending_funding_remains_explicit_and_unavailable_before_action_bullets():
    facts = {
        "accounts": [{"account_id": "ISA", "account_type": "ISA"}],
        "receipts": [
            {
                "receipt_id": "PENDING",
                "description": "Inheritance from the estate",
                "status": "pending",
                "amount": {"value": "27000", "precision": "approximate"},
            }
        ],
        "actions": [
            {
                "kind": "contribute",
                "status": "conditional",
                "destination_account_ids": ["ISA"],
                "source_funding_ids": ["PENDING"],
                "conditions": ["Confirm the remaining ISA allowance"],
            }
        ],
    }
    original = deepcopy(facts)
    text = render_slot("action_plan", facts, {})
    context = text.split("- Contribute", 1)[0]
    assert "pending" in context.lower()
    assert "around £27,000" in context
    assert "excluded from available funding" in context
    assert "Confirm the remaining ISA allowance" in text
    assert "Do not implement" in text
    assert facts == original


def test_split_layout_keeps_active_confirmations_and_deferred_status_exactly_once():
    facts = {
        "accounts": [{"account_id": "ISA", "account_type": "ISA"}],
        "actions": [
            {"kind": "retain", "source_account_id": "ISA", "status": "agreed"},
            {
                "kind": "confirm",
                "rationale": "Confirm the payment date",
                "timing": "Before approval",
                "conditions": ["Check the source record"],
                "status": "conditional",
            },
            {
                "kind": "contribute",
                "destination_account_ids": ["ISA"],
                "status": "future",
                "timing": "Next annual review",
            },
        ],
    }
    plan = render_slot("action_plan", facts, {})
    queries = render_slot("adviser_queries", facts, {})
    assert "Confirm the payment date" not in plan
    assert queries.count("Confirm the payment date") == 1
    assert "Before approval" in queries and "Check the source record" in queries
    assert "Do not implement" in queries
    assert "Not a current recommendation" in plan
    assert "Next annual review" in plan
    assert not any(
        line.startswith("- ") and "Not a current" in line for line in plan.splitlines()
    )
