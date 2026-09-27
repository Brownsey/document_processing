"""Independent wrapper and planned-row checks for review-feedback corrections."""

import pytest
from test_loop_domain import evidence, ref

from agent_pipeline.contracts import Ok
from agent_pipeline.domain import CaseFacts, reconcile
from agent_pipeline.evaluation import score_report
from agent_pipeline.rendering import render_slot


@pytest.mark.parametrize(
    "wrapper",
    [
        "Lifetime ISA",
        "Junior ISA",
        "JISA",
        "LISA",
        "Help to Buy ISA",
        "Help-to-Buy ISA",
    ],
)
def test_transfer_into_special_isa_retains_manual_capacity_condition(wrapper):
    text = f"Transfer the existing ISA to a new {wrapper}."
    facts = CaseFacts(
        planned_accounts=[
            {
                "account_id": "NEW",
                "account_type": wrapper,
                "owners": ["Alex"],
                "refs": ref(text),
            }
        ],
        actions=[
            {
                "action_id": "move",
                "kind": "transfer",
                "source_account_id": "ISA",
                "destination_account_ids": ["NEW"],
                "refs": ref(text),
            }
        ],
    )
    result = reconcile(facts, evidence(text))
    assert isinstance(result, Ok)
    assert any(item.code == "isa_capacity" for item in result.value.review_items)
    action = result.value.actions[0]
    assert any(
        "remaining allowance" in condition and "tax year" in condition
        for condition in action.conditions
    )
    rendered = render_slot(
        "action_plan",
        result.value.model_dump(mode="json"),
        {"adviser_confirmations": "discrepancy"},
    )
    assert "remaining allowance" in rendered


@pytest.mark.parametrize(
    "owners,kind,accepted",
    [
        ("Alex and Jo", "Investment account", True),
        ("Mallory", "Investment account", False),
        ("Alex and Jo", "SIPP", False),
    ],
)
def test_planned_table_details_must_match_supported_account(owners, kind, accepted):
    expected = {
        "accounts": [],
        "actions": [{"kind": "open", "destination": ["joint investment account"]}],
        "tax": False,
        "initial_rate": "0",
    }
    manifest = {
        "facts": {
            "requested_account_ids": ["NEW"],
            "planned_accounts": [
                {
                    "account_id": "NEW",
                    "account_type": "Investment account",
                    "owners": ["Alex", "Jo"],
                }
            ],
            "actions": [
                {"kind": "open", "status": "agreed", "destination_account_ids": ["NEW"]}
            ],
        }
    }
    report = (
        "| Account | Owner | Type | Value |\n|---|---|---|---|\n"
        f"| Proposed joint investment account | {owners} | {kind} | Not yet opened |"
    )
    checks = score_report(report, manifest, expected)["checks"]
    assert (
        all(check["passed"] for check in checks if check["category"] == "accounts")
        is accepted
    )


@pytest.mark.parametrize("platform", ["Planned Wealth", "Proposed Investments"])
def test_existing_platform_prefix_does_not_reclassify_real_holding(platform):
    account = {
        "account_id": "REAL-ISA",
        "account_type": "ISA",
        "platform": platform,
        "owners": ["Alex"],
        "status": "open",
        "valuation": {"value": "10000", "effective_date": "2026-04-30"},
    }
    facts = {"accounts": [account], "requested_account_ids": ["REAL-ISA"]}
    expected = {
        "accounts": [
            {
                "id": "REAL-ISA",
                "aliases": ["REAL-ISA"],
                "owners": ["Alex"],
                "value": "10000",
            }
        ],
        "actions": [],
        "tax": False,
        "initial_rate": "0",
    }
    report = (
        "# Report\n\n## Introduction\nYour REAL-ISA.\n\n## Background & Objectives\n"
        + render_slot("holdings", facts, {})
    )
    checks = score_report(report, {"facts": facts}, expected)["checks"]
    assert all(check["passed"] for check in checks if check["category"] == "accounts")


def test_duplicate_planned_row_cannot_replace_second_recipient():
    facts = {
        "requested_account_ids": ["NEW-A", "NEW-J"],
        "planned_accounts": [
            {"account_id": key, "account_type": "ISA", "owners": [owner]}
            for key, owner in [("NEW-A", "Alex"), ("NEW-J", "Jo")]
        ],
        "actions": [
            {"kind": "open", "status": "agreed", "destination_account_ids": [key]}
            for key in ["NEW-A", "NEW-J"]
        ],
    }
    expected = {
        "accounts": [],
        "actions": [
            {"kind": "open", "destination": [f"{owner} ISA", "ISA"]}
            for owner in ["Alex", "Jo"]
        ],
        "tax": False,
        "initial_rate": "0",
    }
    report = (
        "| Account | Owner | Type | Value |\n|---|---|---|---|\n"
        + "| Proposed ISA | Alex | ISA | Not yet opened |\n" * 2
    )
    checks = score_report(report, {"facts": facts}, expected)["checks"]
    assert not all(
        check["passed"] for check in checks if check["category"] == "accounts"
    )


def test_planned_owner_labels_target_fees_queries_and_joint_scope_without_table_duplication():
    from copy import deepcopy

    facts = {
        "planned_accounts": [
            {"account_id": "NEW-A", "account_type": "ISA", "owners": ["Alex"]},
            {"account_id": "NEW-J", "account_type": "ISA", "owners": ["Jo"]},
            {
                "account_id": "NEW-JOINT",
                "account_type": "Investment account (Joint)",
                "owners": ["Alex", "Jo"],
            },
        ],
        "requested_account_ids": ["NEW-A", "NEW-J", "NEW-JOINT"],
        "fees": [
            {
                "kind": "platform",
                "rate_percent": "0.25",
                "confirmed": True,
                "account_ids": ["NEW-A"],
            }
        ],
        "review_items": [
            {
                "code": "eligibility",
                "message": "Confirm eligibility",
                "account_ids": ["NEW-J"],
            }
        ],
        "actions": [{"kind": "open", "destination_account_ids": ["NEW-JOINT"]}],
    }
    original = deepcopy(facts)
    fees = render_slot("fees", facts, {})
    assert "0.25% (basis requires confirmation) for ISA for Alex." in fees
    assert "for ISA for Jo, joint investment account for Alex and Jo" in fees
    queries = render_slot("adviser_queries", facts, {})
    assert "the proposed ISA for Jo" in queries and "Alex" not in queries
    scope = render_slot("scope", facts, {})
    assert scope.count("for Alex and Jo") == 1
    assert "Open a joint investment account for Alex and Jo." in render_slot(
        "action_plan", facts, {}
    )
    table = render_slot("holdings", facts, {})
    assert "| Proposed joint investment account | Alex and Jo |" in table
    assert "for Alex" not in table and "for Jo" not in table
    for rendered in [fees, queries, scope, table]:
        assert "NEW-" not in rendered
    assert facts == original
