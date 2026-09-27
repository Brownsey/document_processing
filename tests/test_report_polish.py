import pytest

from agent_pipeline.evaluation.actions import _action_matches
from agent_pipeline.reporting.rendering import render_slot


@pytest.mark.parametrize("identifier", ["NEW-ACCOUNT", "OPEN-FUND", "opaque_42"])
def test_opening_and_funding_are_distinct_actions_regardless_of_identifier(identifier):
    opening = {"kind": "open", "destination": ["joint investment account"]}
    funding = {"kind": "contribute", "destination": ["joint investment account"]}
    contribute = f"Contribute an amount to be confirmed to the new joint investment account ({identifier})."
    create = f"Open the proposed joint investment account ({identifier})."
    assert not _action_matches(contribute, opening)
    assert _action_matches(contribute, funding)
    assert _action_matches(create, opening)
    assert not _action_matches(create, funding)
    assert not _action_matches("Do not open the joint investment account.", opening)


def planned_case():
    return {
        "accounts": [
            {
                "account_id": "ISA-A",
                "platform": "Cedar",
                "account_type": "ISA",
                "owners": ["Alex"],
            },
            {
                "account_id": "ISA-B",
                "platform": "Cedar",
                "account_type": "ISA",
                "owners": ["Jo"],
            },
        ],
        "planned_accounts": [
            {
                "account_id": "INTERNAL-42",
                "account_type": "Investment account",
                "owners": ["Alex", "Jo"],
                "status": "planned",
            }
        ],
        "requested_account_ids": ["ISA-A", "ISA-B", "INTERNAL-42"],
        "actions": [{"kind": "open", "destination_account_ids": ["INTERNAL-42"]}],
    }


def test_same_type_planned_accounts_keep_contributions_and_exclusions_with_owner():
    facts = {
        "planned_accounts": [
            {"account_id": "NEW-A", "account_type": "ISA", "owners": ["Alex"]},
            {"account_id": "NEW-J", "account_type": "ISA", "owners": ["Jo"]},
        ],
        "actions": [
            {
                "kind": "contribute",
                "destination_account_ids": [identifier],
                "amount": {"value": amount},
            }
            for identifier, amount in [("NEW-A", "10000"), ("NEW-J", "5000")]
        ]
        + [
            {
                "kind": "exclude",
                "status": "excluded",
                "destination_account_ids": ["NEW-J"],
                "rationale": "A later top-up is outside this report",
            }
        ],
    }
    text = render_slot("action_plan", facts, {})
    lines = text.splitlines()
    alex = next(line for line in lines if "£10,000" in line)
    jo = next(line for line in lines if "£5,000" in line)
    excluded = next(line for line in lines if "outside this report" in line)
    assert "Alex" in alex and "Jo" not in alex
    assert "Jo" in jo and "Alex" not in jo
    assert "Jo" in excluded and "Alex" not in excluded
    assert "NEW-A" not in text and "NEW-J" not in text


def test_planned_ids_stay_internal_and_scope_groups_existing_accounts():
    facts = planned_case()
    scope = render_slot("scope", facts, {})
    assert "Cedar ISAs (ISA-A and ISA-B)" in scope
    assert "Alex and Jo" in scope
    for renderer in ("scope", "actions", "fees", "holdings"):
        assert "INTERNAL-42" not in render_slot(renderer, facts, {})
    assert "joint investment account" in render_slot("actions", facts, {})
    table = render_slot("holdings", facts, {})
    assert "Proposed joint investment account" in table
    assert "Not yet opened" in table and "INTERNAL-42" not in table


def test_review_consolidation_keeps_status_and_other_account_requirements():
    facts = {
        "accounts": [
            {"account_id": key, "platform": "Cedar", "account_type": "Cash"}
            for key in ("A", "B")
        ],
        "review_items": [
            {
                "code": "source_request",
                "message": "Confirm whether the account is active and establish its balance.",
                "account_ids": ["A"],
            },
            {
                "code": "missing_balance",
                "message": "Confirm the outstanding account balance before finalising.",
                "account_ids": ["A"],
            },
            {
                "code": "missing_balance",
                "message": "Confirm the outstanding account balance before finalising.",
                "account_ids": ["B"],
            },
            {
                "code": "timing",
                "message": "Confirm the repayment date.",
                "account_ids": ["A"],
            },
        ],
    }
    text = render_slot("actions", facts, {})
    assert text.count("[REVIEW REQUIRED:") == 3
    assert "active" in text and "balance" in text and "repayment date" in text
    assert "Cedar Cash (B)" in text


def test_identical_allocation_reviews_combine_accounts_without_losing_coverage():
    facts = planned_case()
    facts["review_items"] = [
        {
            "code": "allocation_confirmation",
            "message": "Confirm allocation amounts before implementation.",
            "account_ids": [key],
        }
        for key in facts["requested_account_ids"]
    ]
    text = render_slot("actions", facts, {})
    assert text.count("Confirm allocation amounts") == 1
    assert "ISA-A" in text and "ISA-B" in text and "joint investment account" in text


def test_unknown_fee_controls_share_coverage_and_preserve_both_charge_types():
    text = render_slot("fees", planned_case(), {})
    assert text.count("[REVIEW REQUIRED:") == 1
    assert "platform charge" in text and "ongoing advice charge" in text
    assert "all accounts covered" in text and "proposed" in text and "Cedar" in text
    assert "Cedar" in text[text.index("[REVIEW REQUIRED:") :]


def test_received_funding_uses_a_label_and_names_the_pooled_source():
    facts = planned_case()
    facts["receipts"] = [
        {
            "receipt_id": "R",
            "description": "Inheritance received by Jo",
            "status": "received",
            "amount": {"value": "92000", "precision": "approximate"},
        }
    ]
    facts["actions"] = [
        {"kind": "dispose", "source_account_id": "ISA-A", "extent": "full"},
        {
            "kind": "contribute",
            "source_account_id": "ISA-A",
            "source_funding_ids": ["R"],
            "destination_account_ids": ["INTERNAL-42"],
        },
    ]
    text = render_slot("actions", facts, {})
    assert "Funds received: Inheritance received by Jo" in text
    assert "linked received funds" not in text
    assert "with Inheritance received by Jo" in text
    assert "around £92,000" in text


def test_planned_destination_has_article_but_opening_is_not_doubled():
    facts = planned_case()
    facts["actions"].append(
        {"kind": "contribute", "destination_account_ids": ["INTERNAL-42"]}
    )
    text = render_slot("actions", facts, {})
    assert "Open a joint investment account for Alex and Jo." in text
    assert "to the proposed joint investment account for Alex and Jo." in text


@pytest.mark.parametrize("verb,kind", [("open", "open"), ("contribute", "contribute")])
def test_affirmative_modal_advice_preserves_negation(verb, kind):
    expected = {"kind": kind, "destination": ["joint investment account"]}
    assert _action_matches(
        f"We recommend that you {verb} the joint investment account.", expected
    )
    assert _action_matches(
        f"We propose to {verb} the joint investment account.", expected
    )
    assert not _action_matches(
        f"We do not recommend that you {verb} the joint investment account.", expected
    )
    assert not _action_matches(
        f"We recommend that you do not {verb} the joint investment account.", expected
    )


@pytest.mark.parametrize(
    "destinations", [["ONE-ISA"], ["ONE-ISA", "TWO-ISA"], ["ONE-ISA", "OTHER-ISA"]]
)
def test_extraction_contribution_checks_each_recipient_identity(destinations):
    from agent_pipeline.evaluation.scoring import score_report

    expected = {
        "tax": False,
        "initial_rate": "0",
        "allowed_amounts": [],
        "accounts": [
            {"id": key, "aliases": [key], "owners": [], "value": None}
            for key in ["ONE-ISA", "TWO-ISA"]
        ],
        "actions": [
            {"kind": "contribute", "destination": [key, "ISAs"]}
            for key in ["ONE-ISA", "TWO-ISA"]
        ],
    }
    facts = {
        "accounts": [
            {"account_id": key, "account_type": "ISA"}
            for key in ["ONE-ISA", "TWO-ISA", "OTHER-ISA"]
        ],
        "actions": [
            {
                "kind": "contribute",
                "status": "agreed",
                "destination_account_ids": destinations,
            }
        ],
    }
    result = score_report("", {"facts": facts}, expected)
    checks = {item["name"]: item["passed"] for item in result["checks"]}
    assert checks["agreed action 1"]
    assert checks["agreed action 2"] == ("TWO-ISA" in destinations)


def test_rationale_receives_only_relevant_account_types_and_stated_purpose():
    from agent_pipeline.pipeline.prompt_inputs import select_facts

    facts = {
        "accounts": [
            {
                "account_id": "CASH-X",
                "account_type": "Cash Account",
                "valuation": {"value": "73123"},
            },
            {"account_id": "ISA-X", "account_type": "Stocks & Shares ISA"},
            {"account_id": "OTHER", "account_type": "Unrelated bond"},
        ],
        "actions": [
            {
                "kind": "transfer",
                "source_account_id": "CASH-X",
                "destination_account_ids": ["ISA-X"],
                "rationale": "Use the current ISA allowance.",
                "amount": {"value": "12345"},
            }
        ],
        "narratives": [
            {"category": "risk", "text": "Moderately adventurous (risk profile 6)."}
        ],
    }
    selected = select_facts("rationale", facts)
    action = selected["actions"][0]
    assert action["source_account_type"] == "Cash Account"
    assert action["destination_account_types"] == ["Stocks & Shares ISA"]
    assert action["rationale"] == "Use the current ISA allowance."
    assert all(
        value not in str(selected)
        for value in ["CASH-X", "ISA-X", "OTHER", "73123", "12345", "Unrelated bond"]
    )
    assert (
        select_facts("background", facts)["narratives"][0]["text"]
        == "Moderately adventurous (risk profile 6)."
    )


@pytest.mark.parametrize(
    ("sentence", "passes"),
    [
        ("Confirm the remaining allowance.", True),
        ("Confirm the remaining allowances.", True),
        ("The remaining allowances are available.", False),
    ],
)
def test_review_concepts_accept_plural_but_still_require_confirmation(sentence, passes):
    from agent_pipeline.evaluation.scoring import score_report

    result = score_report(
        "## Recommendations\n" + sentence,
        {},
        {
            "accounts": [],
            "actions": [],
            "tax": False,
            "initial_rate": "0",
            "review_concepts": [["allowance", "remaining"]],
        },
    )
    check = next(
        c for c in result["checks"] if c["name"] == "confirmation: allowance/remaining"
    )
    assert check["passed"] is passes


@pytest.mark.parametrize("selector", ["rationale", "recommendations"])
def test_charge_questions_stay_out_of_recommendation_inputs_without_losing_fee_controls(
    selector,
):
    from copy import deepcopy

    from agent_pipeline.pipeline.prompt_inputs import select_facts

    facts = {
        "narratives": [
            {"category": "objective", "text": "Simplify the investments."},
            {
                "category": "charges_concern",
                "text": "Alex asked about ongoing charges.",
            },
            {"category": "sensitivity", "text": "Jo asked about platform costs."},
        ],
        "fees": [],
    }
    original = deepcopy(facts)
    assert select_facts(selector, facts)["narratives"] == [facts["narratives"][0]]
    assert facts == original
    fees = render_slot("fees", facts, {})
    assert "platform charge" in fees and "ongoing advice charge" in fees
    assert "REVIEW REQUIRED" in fees
