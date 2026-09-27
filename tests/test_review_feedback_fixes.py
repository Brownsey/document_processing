import json

import pytest
from test_loop_domain import evidence, ref
from test_report_polish import planned_case
from test_workflow import CaseProvider, configured_case

from agent_pipeline.contracts import Err, Ok
from agent_pipeline.domain import CaseFacts, reconcile
from agent_pipeline.evaluation import score_report
from agent_pipeline.rendering import render_slot
from agent_pipeline.workflow import run_generation


def test_excluded_aspiration_is_prose_without_an_invented_funding_query():
    facts = planned_case()
    facts["actions"] = [
        {
            "action_id": "future-goal",
            "kind": "exclude",
            "status": "excluded",
            "rationale": "The future travel plans are outside this report.",
        }
    ]
    text = render_slot("action_plan", facts, {})
    assert "The future travel plans are outside this report." in text
    assert "amount to be confirmed" not in text
    assert not any(line.startswith("- ") for line in text.splitlines())


def test_scoped_planned_account_has_a_table_row_without_invented_holdings():
    facts = planned_case()
    facts["planned_accounts"].append(
        {"account_id": "OUTSIDE", "account_type": "SIPP", "owners": ["Alex"]}
    )
    text = render_slot("holdings", facts, {})
    row = next(line for line in text.splitlines() if "joint investment account" in line)
    assert "Proposed" in row and "Not yet opened" in row
    assert "Alex and Jo" in row
    assert "£" not in row and "INTERNAL-42" not in row
    assert "SIPP" not in text


def test_excluded_destination_remains_identifiable_with_its_conditions():
    facts = planned_case()
    facts["actions"] = [
        {
            "kind": "exclude",
            "status": "excluded",
            "destination_account_ids": ["ISA-A"],
            "rationale": "Leave this account outside the current advice.",
            "timing": "Until the next review",
            "conditions": ["Await the client's decision"],
        }
    ]
    text = render_slot("action_plan", facts, {})
    assert "Cedar ISA (ISA-A)" in text
    assert "Until the next review" in text
    assert "Await the client's decision" in text
    assert "amount to be confirmed" not in text


@pytest.mark.parametrize("planned", [False, True])
@pytest.mark.parametrize("mixed", [False, True])
def test_isa_subscription_requires_allowance_check_without_part_funded_wording(
    planned, mixed
):
    text = "Contribute to Alex's ISA, with the balance to a new investment account."
    target = "NEW-ISA" if planned else "ISA"
    planned_accounts = [
        {
            "account_id": "NEW",
            "account_type": "Investment account",
            "owners": ["Alex"],
            "refs": ref(text),
        }
    ]
    if planned:
        planned_accounts.append(
            {
                "account_id": target,
                "account_type": "ISA",
                "owners": ["Alex"],
                "refs": ref(text),
            }
        )
    facts = CaseFacts(
        planned_accounts=planned_accounts,
        actions=[
            {
                "action_id": "fund",
                "kind": "contribute",
                "source_account_id": "GIA",
                "destination_account_ids": [target] + (["NEW"] if mixed else []),
                "refs": ref(text),
            }
        ],
    )
    result = reconcile(facts, evidence(text))
    assert isinstance(result, Ok), result
    item = next(i for i in result.value.review_items if i.code == "isa_capacity")
    assert item.account_ids == [target]
    assert "remaining" in item.message and "tax year" in item.message
    assert not any(i.code == "isa_funding_conflict" for i in result.value.review_items)
    # Capacity is an implementation condition even when routine queries are hidden.
    rendered = render_slot(
        "action_plan",
        result.value.model_dump(mode="json"),
        {"adviser_confirmations": "discrepancy"},
    )
    assert "remaining allowance" in rendered and "tax year" in rendered


@pytest.mark.parametrize("status", ["future", "excluded"])
def test_inactive_isa_plan_does_not_gain_implementation_conditions(status):
    text = "Consider contributing to the ISA later."
    facts = CaseFacts(
        actions=[
            {
                "action_id": "later",
                "kind": "contribute",
                "source_account_id": "GIA",
                "destination_account_ids": ["ISA"],
                "status": status,
                "refs": ref(text),
            }
        ]
    )
    result = reconcile(facts, evidence(text))
    assert isinstance(result, Ok)
    assert not any(i.code == "isa_capacity" for i in result.value.review_items)
    assert not result.value.actions[0].conditions


def test_isa_to_isa_transfer_does_not_gain_new_subscription_condition():
    text = "Transfer the existing ISA to the new ISA."
    facts = CaseFacts(
        planned_accounts=[
            {
                "account_id": "NEW-ISA",
                "account_type": "ISA",
                "owners": ["Alex"],
                "refs": ref(text),
            }
        ],
        actions=[
            {
                "action_id": "move",
                "kind": "transfer",
                "source_account_id": "ISA",
                "destination_account_ids": ["NEW-ISA"],
                "refs": ref(text),
            }
        ],
    )
    result = reconcile(facts, evidence(text))
    assert isinstance(result, Ok)
    assert not any(i.code == "isa_capacity" for i in result.value.review_items)
    assert not result.value.actions[0].conditions


def test_transfer_into_lifetime_isa_still_requires_limit_confirmation():
    text = "Transfer the existing ISA to a new Lifetime ISA."
    facts = CaseFacts(
        planned_accounts=[
            {
                "account_id": "LISA",
                "account_type": "Lifetime ISA",
                "owners": ["Alex"],
                "refs": ref(text),
            }
        ],
        actions=[
            {
                "action_id": "move",
                "kind": "transfer",
                "source_account_id": "ISA",
                "destination_account_ids": ["LISA"],
                "refs": ref(text),
            }
        ],
    )
    result = reconcile(facts, evidence(text))
    assert isinstance(result, Ok)
    assert any(i.code == "isa_capacity" for i in result.value.review_items)


@pytest.mark.parametrize(
    "row,passed",
    [
        (
            "| Proposed joint investment account | Alex and Jo | Investment account | Not yet opened |",
            True,
        ),
        (
            "| Proposed joint investment account | Alex and Jo | Investment account | £5,000 |",
            False,
        ),
        (
            "| Proposed mystery account | Alex | Investment account | Not yet opened |",
            False,
        ),
        (
            "| Proposed InventedPlatform joint investment account | Alex and Jo | Investment account | Not yet opened |",
            False,
        ),
        (
            "| Proposed joint investment account (NEW) | Alex and Jo | Investment account | Not yet opened |",
            False,
        ),
        ("", False),
    ],
)
def test_scorer_checks_planned_rows_separately_from_existing_holdings(row, passed):
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
                    "owners": ["Alex", "Jo"],
                    "account_type": "Investment account",
                }
            ],
        }
    }
    result = score_report(
        "| Account | Owner | Type | Value |\n|---|---|---|---|\n" + row,
        manifest,
        expected,
    )
    checks = [c for c in result["checks"] if c["category"] == "accounts"]
    assert all(c["passed"] for c in checks) is passed


def test_code_only_review_issue_stops_without_futile_fact_reextraction(tmp_path):
    class CodeIssue(CaseProvider):
        def complete(self, **request):
            result = super().complete(**request)
            if request["task"] == "validate_support":
                result.value.data.update(
                    supported=False,
                    issue_kind="code",
                    issues=[
                        "A fixed template sentence contradicts the report specification."
                    ],
                )
            return result

    client, config, output, _ = configured_case(tmp_path)
    provider = CodeIssue()
    result = run_generation(
        client_dir=client, config_path=config, output_dir=output, provider=provider
    )
    assert isinstance(result, Err)
    assert result.error.code == "unsupported_report"
    assert result.error.details["issue_kind"] == "code"
    assert sum(call["task"] == "extract" for call in provider.calls) == 1
    assert not (output / "client.md").exists()
    manifest = json.loads((output / "client.manifest.json").read_text(encoding="utf-8"))
    assert manifest["repair_counts"] == {"facts": 0, "narrative": 0}
    assert "fixed template" in manifest["error"]["details"]["issues"][0]
