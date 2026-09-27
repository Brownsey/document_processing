"""Independent edge cases for the ISA-only disposal implementation pause."""

from datetime import date

import pytest
from test_isa_disposal import apply, case

from agent_pipeline.reporting.rendering import render_slot
from agent_pipeline.rules import domain, models


@pytest.mark.parametrize(
    "timing",
    [
        "In the 2027/28 tax year",
        "Next tax year",
        "On 6 April 2027",
        "On 06/04/2027",
        "On 6-4-2027",
        "On 06.04.2027",
        "On 06/04/27",
    ],
)
def test_later_subscription_year_cannot_inherit_meeting_year_numeric_limit(timing):
    facts = case()
    facts.effective_date = date(2026, 5, 14)
    facts.actions[1].timing = timing
    apply(facts)
    warning = next(i for i in facts.review_items if i.code == "isa_funding_conflict")
    assert "40,000" not in warning.message
    assert all(action.status == "conditional" for action in facts.actions)
    assert facts.actions[1].timing == timing


@pytest.mark.parametrize("kind", ["Junior ISA", "Lifetime ISA", "Help to Buy ISA"])
def test_special_wrapper_does_not_inherit_generic_adult_numeric_capacity(kind):
    facts = case()
    for target in facts.accounts[1:]:
        target.account_type = kind
    apply(facts)
    warning = next(i for i in facts.review_items if i.code == "isa_funding_conflict")
    assert "40,000" not in warning.message
    assert "limits" in warning.message
    assert all(action.status == "conditional" for action in facts.actions)


def test_unknown_recipient_ownership_keeps_pause_without_numeric_capacity():
    facts = case()
    facts.accounts[1].owners = []
    apply(facts)
    assert "40,000" not in facts.review_items[0].message
    assert all(action.status == "conditional" for action in facts.actions)


def test_capacity_review_with_distinct_condition_is_not_hidden_by_general_pause():
    facts = apply(case())
    facts.review_items.append(
        models.ReviewItem(
            code="isa_capacity",
            message="Confirm ISA subscription eligibility after the client's tax residence changes.",
            account_ids=["ISA-0"],
        )
    )
    text = render_slot("actions", facts.model_dump(mode="json"), {})
    assert "tax residence changes" in text


def test_administrative_review_heading_does_not_turn_it_into_implementation_condition():
    facts = case()
    facts.review_items = [
        models.ReviewItem(
            code="source_request",
            message="Confirm the statement delivery address before finalising.",
            account_ids=["ISA-0"],
        )
    ]
    text = render_slot("actions", facts.model_dump(mode="json"), {})
    assert "before finalising" in text
    assert "**Before implementation:**" not in text


def test_receipt_linked_disposal_funding_keeps_both_actions_paused():
    from support.evidence import evidence, ref

    from agent_pipeline.contracts import Ok

    text = "On 14 May 2026 sell the GIA in full and use the pending proceeds to fund the ISA."
    facts = models.CaseFacts(
        effective_date="2026-05-14",
        actions=[
            models.Action(
                action_id="sale",
                kind="dispose",
                source_account_id="GIA",
                extent="full",
                refs=ref(text),
            ),
            models.Action(
                action_id="fund",
                kind="contribute",
                status="conditional",
                source_funding_ids=["PROCEEDS"],
                destination_account_ids=["ISA"],
                refs=ref(text),
            ),
        ],
        receipts=[
            models.Receipt(
                receipt_id="PROCEEDS",
                description="Pending GIA sale proceeds",
                status="pending",
                source_account_id="GIA",
                refs=ref(text),
            )
        ],
    )
    result = domain.reconcile(facts, evidence(text))
    assert isinstance(result, Ok), result
    assert all(action.status == "conditional" for action in result.value.actions)
    assert any(
        item.code == "isa_funding_conflict" for item in result.value.review_items
    )
