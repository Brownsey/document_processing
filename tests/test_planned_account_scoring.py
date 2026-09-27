import pytest

from agent_pipeline.evaluation.scoring import score_report


@pytest.mark.parametrize(
    "owners,kind,destinations,passed",
    [
        (["Alex", "Jo"], "Investment account", ["NEW"], True),
        (["Alex"], "Investment account", ["NEW"], False),
        (["Alex", " alex "], "Investment account", ["NEW"], False),
        (["Alex", "Jo"], "Savings account", ["NEW"], False),
        (["Alex", "Jo"], "Investment account", ["ELSEWHERE"], False),
    ],
)
def test_planned_funding_matches_actual_account_type_and_distinct_owners(
    owners, kind, destinations, passed
):
    expected = {
        "accounts": [],
        "actions": [
            {"kind": "contribute", "destination": ["joint investment account"]}
        ],
        "tax": False,
        "initial_rate": "0",
    }
    manifest = {
        "facts": {
            "planned_accounts": [
                {"account_id": "NEW", "account_type": kind, "owners": owners}
            ],
            "actions": [
                {
                    "kind": "contribute",
                    "status": "agreed",
                    "destination_account_ids": destinations,
                }
            ],
        }
    }
    result = score_report("", manifest, expected)
    check = next(c for c in result["checks"] if c["name"] == "agreed action 1")
    assert check["passed"] is passed
