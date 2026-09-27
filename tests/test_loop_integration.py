"""Regression checks for recovered report-loop requirements in the native pipeline."""

from agent_pipeline.pipeline.prompt_inputs import select_facts


def test_background_selector_excludes_recommendation_and_exclusion_facts():
    facts = {
        "narratives": [
            {"category": "circumstance", "text": "Recent bereavement"},
            {"category": "objective", "text": "Long-term flexibility"},
            {"category": "sensitivity", "text": "Asked about platform charges"},
            {"category": "exclusion", "text": "Do not discuss review task"},
        ]
    }

    selected = select_facts("background", facts)

    assert selected["narratives"] == facts["narratives"][:2]
    assert "actions" not in selected
    assert "accounts" not in selected


def test_recommendations_selector_drops_charge_questions_and_unrelated_exclusions():
    charge_question = {
        "category": "sensitivity",
        "text": "The client asked about platform charges",
    }
    exclusion = {"category": "exclusion", "text": "Unrelated procedural note"}
    facts = {
        "narratives": [charge_question, exclusion],
        "actions": [{"action_id": "a1", "rationale": "Simplify the plan"}],
        "accounts": [{"account_id": "ISA-1"}],
    }

    selected = select_facts("recommendations", facts)

    assert selected["narratives"] == []
    assert selected["actions"] == facts["actions"]


def test_rationale_projects_reasons_and_account_types_without_charge_questions():
    facts = {
        "narratives": [
            {"category": "objective", "text": "Future income"},
            {
                "category": "charges_concern",
                "text": "Jo asked about ongoing charges",
                "refs": [{"excerpt": "£500000 cash"}],
            },
            {"category": "circumstance", "text": "Retired"},
        ],
        "actions": [
            {
                "kind": "contribute",
                "rationale": "Use ISA allowances",
                "amount": {"value": 12345},
                "refs": [{"excerpt": "£12345"}],
            }
        ],
        "review_items": [{"message": "Confirm balance £12345"}],
        "receipts": [{"amount": {"value": 500000}}],
    }
    selected = select_facts("rationale", facts)
    assert selected["actions"] == [
        {
            "kind": "contribute",
            "rationale": "Use ISA allowances",
            "source_account_type": "",
            "destination_account_types": [],
        }
    ]
    assert [n["text"] for n in selected["narratives"]] == [
        "Future income",
    ]
    assert not {"review_items", "receipts", "accounts", "conflicts"} & selected.keys()
    assert "12345" not in str(selected) and "500000" not in str(selected)
