from copy import deepcopy
from decimal import Decimal

import pytest

from agent_pipeline.reporting.rendering import render_slot
from agent_pipeline.rules import domain, models
from agent_pipeline.rules.isa_funding import check_isa_disposal_plans


def case(
    *, value="47000", precision="lower_bound", owners=("Alex", "Jo"), tax_year="2026/27"
):
    accounts = [
        models.Account(
            account_id="GIA-X",
            account_type="GIA",
            owners=["Alex", "Jo"],
            valuation=models.Money(value=value, precision=precision),
        )
    ]
    accounts += [
        models.Account(
            account_id=f"ISA-{index}",
            account_type="Stocks & Shares ISA",
            owners=[owner],
        )
        for index, owner in enumerate(owners)
    ]
    facts = models.CaseFacts(
        tax_year=tax_year,
        accounts=accounts,
        actions=[
            models.Action(
                action_id="sale",
                kind="dispose",
                source_account_id="GIA-X",
                extent="full",
            ),
            models.Action(
                action_id="fund",
                kind="contribute",
                source_account_id="GIA-X",
                destination_account_ids=[a.account_id for a in accounts[1:]],
                allocation_rule="Split equally",
            ),
        ],
    )
    return facts


def apply(facts):
    check_isa_disposal_plans(
        facts, {a.account_id: a for a in facts.accounts + facts.planned_accounts}
    )
    return facts


def test_full_sale_above_two_person_ceiling_requires_adviser_decision_not_new_sale_amount():
    facts = apply(case())
    warning = next(
        item for item in facts.review_items if item.code == "isa_funding_conflict"
    )
    assert "40,000" in warning.message and "2026/27" in warning.message
    assert "valuation" in warning.message and "exceeds" in warning.message
    assert "subscriptions" in warning.message and "surplus" in warning.message
    assert all(action.status == "conditional" for action in facts.actions)
    assert facts.actions[0].extent == "full"
    assert all(action.amount is None for action in facts.actions)
    text = render_slot("actions", facts.model_dump(mode="json"), {})
    assert text.index("IMPLEMENTATION PAUSED") < text.index("Sell the entire holding")
    assert "Do not implement the disposal or reinvestment" in text
    assert "40,000" in text


def test_multiple_isas_for_one_owner_do_not_multiply_personal_allowance():
    facts = apply(case(value="27000", owners=("Alex", "Alex")))
    message = facts.review_items[0].message
    assert "20,000" in message and "40,000" not in message


@pytest.mark.parametrize("precision", ["approximate", "upper_bound", "unknown"])
def test_uncertain_valuation_is_not_reported_as_proven_excess(precision):
    facts = apply(case(precision=precision))
    assert "exceeds" not in facts.review_items[0].message
    assert all(action.status == "conditional" for action in facts.actions)


@pytest.mark.parametrize("tax_year", [None, "2025/26", "2027/28"])
def test_unverified_tax_year_requires_confirmation_without_borrowing_current_limit(
    tax_year,
):
    facts = apply(case(tax_year=tax_year))
    assert "40,000" not in facts.review_items[0].message
    assert "tax year" in facts.review_items[0].message
    assert facts.actions[0].status == "conditional"


def test_small_value_still_requires_remaining_allowance_not_false_excess():
    facts = apply(case(value="10000", precision="exact"))
    assert "exceeds" not in facts.review_items[0].message
    assert "remaining" in facts.review_items[0].message


@pytest.mark.parametrize("change", ["partial", "mixed", "excluded", "isa_transfer"])
def test_other_plans_are_not_misrepresented_as_full_gia_to_isa_conflicts(change):
    facts = case()
    if change == "partial":
        facts.actions[0].extent = "partial"
    elif change == "mixed":
        facts.planned_accounts = [
            models.Account(
                account_id="NEW", account_type="Investment account", status="planned"
            )
        ]
        facts.actions[1].destination_account_ids.append("NEW")
    elif change == "excluded":
        facts.actions[0].status = "excluded"
    else:
        facts.accounts[0].account_type = "Stocks & Shares ISA"
    original = deepcopy(facts)
    apply(facts)
    assert facts == original


def test_full_sale_warning_is_added_by_reconcile_and_does_not_mutate_input():
    from support.evidence import evidence, ref

    text = "On 14 May 2026 sell the GIA in full and fund the ISA with the proceeds."
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
                source_account_id="GIA",
                destination_account_ids=["ISA"],
                refs=ref(text),
            ),
        ],
    )
    from agent_pipeline.contracts import Ok

    result = domain.reconcile(facts, evidence(text))
    assert isinstance(result, Ok), result
    assert any(
        item.code == "isa_funding_conflict" for item in result.value.review_items
    )
    assert result.value.actions[0].status == "conditional"
    assert facts.actions[0].status == "agreed" and facts.review_items == []
    assert result.value.accounts[0].valuation is not None
    assert result.value.accounts[0].valuation.value == Decimal("30000")


@pytest.mark.parametrize("kind", ["JISA", "LISA"])
def test_abbreviated_special_isa_still_requires_manual_limit_confirmation(kind):
    facts = case()
    for account in facts.accounts[1:]:
        account.account_type = kind
    apply(facts)
    assert facts.review_items and "40,000" not in facts.review_items[0].message
    assert all(action.status == "conditional" for action in facts.actions)
