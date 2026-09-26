from decimal import Decimal


def case():
    return {
        "accounts": [
            {
                "account_id": "ISA-1",
                "platform": "Example",
                "account_type": "ISA",
                "owners": ["Sam"],
                "valuation": {
                    "value": "52000",
                    "currency": "GBP",
                    "effective_date": "2026-04-30",
                    "precision": "approximate",
                },
            },
            {
                "account_id": "CASH-1",
                "platform": "Bank",
                "account_type": "Cash",
                "owners": ["Sam"],
                "valuation": {
                    "value": "25000",
                    "currency": "GBP",
                    "precision": "exact",
                },
            },
        ],
        "requested_account_ids": ["ISA-1"],
        "planned_accounts": [],
        "actions": [
            {
                "action_id": "a1",
                "kind": "contribute",
                "source_account_id": "CASH-1",
                "destination_account_ids": ["ISA-1"],
                "amount": {"value": "20000", "currency": "GBP", "precision": "exact"},
                "extent": "partial",
                "conditions": ["Confirm allowance"],
                "rationale": "Use cash",
                "status": "conditional",
                "refs": [],
            }
        ],
        "review_items": [],
    }


def test_holdings_respects_scope_and_preserves_qualification_date():
    from agent_pipeline.rendering import render_slot

    result = render_slot("holdings", case(), {})
    assert "| Account | Owner | Type | Value |" in result
    assert "around £52,000" in result
    assert "30 April 2026" in result
    assert "Cash" not in result
    assert result.count("ISA-1") == 1


def test_actions_keep_direction_conditions_and_zero_fee():
    from agent_pipeline.rendering import render_slot

    facts = case()
    result = render_slot("actions", facts, {})
    assert "from Bank Cash (CASH-1)" in result
    assert "to Example ISA (ISA-1)" in result
    assert "£20,000" in result
    assert "Confirm allowance" in result
    facts["fees"] = [
        {
            "kind": "initial",
            "rate_percent": Decimal("0"),
            "basis": "ISA contribution",
            "refs": [],
        }
    ]
    assert "0%" in render_slot("fees", facts, {})


def test_scope_lists_planned_accounts_without_balances():
    from agent_pipeline.rendering import render_slot

    facts = case()
    facts["requested_account_ids"].append("NEW-1")
    facts["planned_accounts"] = [
        {
            "account_id": "NEW-1",
            "platform": "Example",
            "account_type": "Joint GIA",
            "owners": ["Sam", "Jo"],
        }
    ]
    text = render_slot("scope", facts, {})
    assert "Sam and Jo" in text
    assert "proposed" in text
    assert "£" not in text
