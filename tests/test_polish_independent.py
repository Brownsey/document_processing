"""Independent regressions for presentation and action-scoring semantics."""

import json
from pathlib import Path

import pytest

from agent_pipeline.evaluation.actions import _action_matches
from agent_pipeline.evaluation.matching import _sentences
from agent_pipeline.reporting.rendering import render_slot


@pytest.mark.parametrize(
    "instruction",
    ["contribute to", "fund", "allocate money to"],
)
def test_negated_funding_never_satisfies_expected_contribution(instruction):
    assert not _action_matches(
        f"Do not {instruction} the joint investment account.",
        {"kind": "contribute", "destination": ["joint investment account"]},
    )


def test_negated_recommendation_does_not_satisfy_opening():
    assert not _action_matches(
        "We do not recommend opening the joint investment account.",
        {"kind": "open", "destination": ["joint investment account"]},
    )


@pytest.mark.parametrize(
    "kind,verb", [("open", "Opening"), ("contribute", "Contributing to")]
)
def test_trailing_negative_recommendation_does_not_pass(kind, verb):
    assert not _action_matches(
        f"{verb} the joint investment account is not recommended.",
        {"kind": kind, "destination": ["joint investment account"]},
    )


def test_opening_with_pending_funding_does_not_satisfy_contribution():
    assert not _action_matches(
        "Open a joint investment account pending funding confirmation.",
        {"kind": "contribute", "destination": ["joint investment account"]},
    )


def test_action_after_digit_ending_account_is_separate_without_splitting_decimals():
    sentences = _sentences(
        "Sell a portion of ACCOUNT-4. Contribute to ACCOUNT-4. The rate is 0.25%."
    )
    assert len(sentences) == 3
    assert "0.25%" in sentences[-1]
    assert any(
        _action_matches(s, {"kind": "contribute", "destination": ["ACCOUNT-4"]})
        for s in sentences
    )


def test_fee_review_cannot_suppress_account_balance_control():
    facts = {
        "accounts": [{"account_id": "A", "account_type": "Cash"}],
        "review_items": [
            {
                "code": "platform_fees",
                "message": "Confirm the platform charge basis against the account balance.",
                "account_ids": ["A"],
            },
            {
                "code": "missing_balance",
                "message": "Confirm the outstanding account balance before finalising.",
                "account_ids": ["A"],
            },
        ],
    }
    assert "Confirm the outstanding account balance before finalising" in render_slot(
        "actions", facts, {}
    )


def test_distinct_review_timing_survives_balance_deduplication():
    facts = {
        "accounts": [{"account_id": "A", "account_type": "Cash"}],
        "review_items": [
            {
                "code": "source_request",
                "message": "Confirm the account balance at the next annual review.",
                "account_ids": ["A"],
            },
            {
                "code": "missing_balance",
                "message": "Confirm the outstanding account balance before finalising.",
                "account_ids": ["A"],
            },
        ],
    }
    text = render_slot("actions", facts, {})
    assert "next annual review" in text
    assert "before finalising" in text


def test_distinct_fee_coverage_is_not_merged_or_lost():
    facts = {
        "accounts": [
            {"account_id": key, "account_type": "ISA", "platform": platform}
            for key, platform in [("A", "Cedar"), ("B", "Birch")]
        ],
        "planned_accounts": [
            {
                "account_id": "INTERNAL-NEW",
                "account_type": "investment account",
                "owners": ["Alex", "Jo"],
            }
        ],
        "requested_account_ids": ["A", "B", "INTERNAL-NEW"],
        "fees": [
            {
                "kind": kind,
                "rate_percent": rate,
                "confirmed": True,
                "basis": "assets held",
                "account_ids": [key],
            }
            for kind, rate, key in [
                ("platform", "0.25", "A"),
                ("platform", "0.35", "B"),
                ("ongoing_advice", "0.6", "A"),
            ]
        ],
    }
    text = render_slot("fees", facts, {})
    assert "0.25% on assets held for Cedar ISA (A)" in text
    assert "0.35% on assets held for Birch ISA (B)" in text
    assert "0.6% on assets held for Cedar ISA (A)" in text
    controls = text.split("[REVIEW REQUIRED:")[1:]
    assert len(controls) == 2
    assert "platform charge" in controls[0]
    assert "joint investment account" in controls[0]
    assert "(A)" not in controls[0] and "(B)" not in controls[0]
    assert "ongoing advice charge" in controls[1]
    assert "Birch ISA (B)" in controls[1]
    assert "joint investment account" in controls[1]
    assert "(A)" not in controls[1]
    assert "INTERNAL-NEW" not in text


@pytest.mark.parametrize(
    "identifiers,grouped",
    [
        (("H4-ISA-J", "H4-ISA-C"), "Contribute to both ISAs."),
        (("B4-SIPP-J", "B4-SIPP-C"), "Contribute to both SIPPs."),
    ],
)
def test_source_required_both_recipients_accept_grouped_and_individual_actions(
    identifiers, grouped
):
    expected = json.loads(Path("eval/development/client_04_stretch.json").read_text())
    actions = [
        action
        for action in expected["actions"]
        if action["kind"] == "contribute"
        and any(key in action["destination"] for key in identifiers)
    ]
    assert len(actions) == 2
    assert all(_action_matches(grouped, action) for action in actions)
    individual = [f"Contribute to the account ({key})." for key in identifiers]
    assert all(
        any(_action_matches(s, action) for s in individual) for action in actions
    )
    for only_one in individual:
        assert sum(_action_matches(only_one, action) for action in actions) == 1
