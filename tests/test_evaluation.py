"""Independent scoring must fail corrupt prose even when its manifest claims success."""

import copy
import json
from pathlib import Path

import pytest

from agent_pipeline.evaluation import runner as evaluation


@pytest.fixture
def case():
    return {
        "client": "synthetic",
        "tax": False,
        "initial_rate": "0",
        "accounts": [
            {
                "id": "A-ISA",
                "aliases": ["A-ISA", "Alpha ISA"],
                "owners": ["Alex Reed"],
                "value": "52000",
                "date": "2026-04-30",
                "precision": "exact",
            }
        ],
        "actions": [
            {
                "kind": "transfer",
                "source": ["cash account", "C-CASH"],
                "destination": ["Alpha ISA", "A-ISA"],
                "amount": "20000",
            }
        ],
        "review_concepts": [["platform"], ["ongoing", "advice"]],
        "required_concepts": [],
        "forbidden_labels": ["Example Growth Fund"],
        "allowed_amounts": ["52000", "20000"],
        "excluded_accounts": ["C-CASH"],
    }


@pytest.fixture
def report():
    return """# Investment Advice Report
## Introduction
We cover your Alpha ISA.
This firm is authorised and regulated by the Financial Conduct Authority.
## Background & Objectives
You seek growth with moderate risk.
| Account | Owner | Type | Value |
| --- | --- | --- | --- |
| A-ISA | Alex Reed | ISA | £52,000 as at 30 April 2026 |
## Recommendations
We recommend moving £20,000 from your cash account into Alpha ISA to use your allowance. No investments are being sold.
## Fees & Charges
Initial charge: 0%. [REVIEW: Confirm the platform charge.] [REVIEW: Confirm the ongoing advice charge.]
## Conclusion
The value of investments can fall as well as rise and you may get back less than you invest. Past performance is not a guide to future returns.
If you are happy to proceed with our recommendations, please let us know and we will implement them for you.
"""


def score(report, case):
    return evaluation.score_report(
        report, {"status": "needs_review", "validation": {"passed": True}}, case
    )


def test_valid_report_and_paraphrase_pass(report, case):
    assert score(report, case)["passed"]
    report = report.replace(
        "moving £20,000 from your cash account into Alpha ISA",
        "topping up Alpha ISA with GBP 20k from your cash account",
    )
    assert score(report, case)["passed"]


@pytest.mark.parametrize(
    ("phrase", "passes"),
    [
        ("deferred-payment", True),
        ("deferred payment", True),
        ("deferredpayment", False),
        ("deferred", False),
        ("unrelated payment", False),
    ],
)
def test_required_prose_concepts_accept_word_hyphens_but_not_missing_words(
    report, case, phrase, passes
):
    case["required_concepts"] = [["deferred payment"]]
    report = report.replace("You seek growth with moderate risk.", phrase + ".")
    assert score(report, case)["passed"] is passes


def test_prose_hyphen_matching_does_not_loosen_identifiers_or_actions(report, case):
    case["required_concepts"] = [["deferred payment"]]
    report = report.replace(
        "You seek growth with moderate risk.", "A deferred-payment is recorded."
    )
    case["accounts"][0]["aliases"] = ["A-ISA"]
    report = report.replace("your Alpha ISA", "your A-ISA")
    assert score(report, case)["passed"]
    assert not score(report.replace("A-ISA", "A ISA"), case)["passed"]
    changed_action = report.replace("into Alpha ISA", "into Alpha-ISA")
    assert not score(changed_action, case)["passed"]


@pytest.mark.parametrize(
    "old,new",
    [
        (
            "£20,000 from your cash account into Alpha ISA",
            "£20,000 from Alpha ISA into your cash account",
        ),
        ("£20,000", "£20,000k"),
        ("£52,000", "£52,000,000"),
        (
            "moving £20,000 from your cash account into Alpha ISA",
            "retaining your Alpha ISA",
        ),
        ("## Recommendations", "## Recommendations\n## Recommendations"),
        (
            "Initial charge: 0%.",
            "Initial charge: 0%. [REVIEW: Platform charge estimated 0.25%.]",
        ),
        (
            "## Fees & Charges",
            "## Tax Implications\nEstimated CGT £2,000. [REVIEW: confirm].\n## Fees & Charges",
        ),
        (
            "We cover your Alpha ISA.",
            "We cover your Alpha ISA. [REVIEW: Insert the FCA wording.]",
        ),
        ("No investments are being sold.", "Sell your Alpha ISA in full."),
    ],
)
def test_corrupt_report_fails_despite_clean_manifest(report, case, old, new):
    assert not score(report.replace(old, new), case)["passed"]


def test_full_partial_and_allowance_condition(report, case):
    case = copy.deepcopy(case)
    case["tax"] = True
    case["actions"] = [{"kind": "sell", "source": ["joint GIA"], "extent": "full"}]
    case["required_concepts"] = [["equal", "equally"], ["allowance", "allowances"]]
    report = report.replace(
        "We recommend moving £20,000 from your cash account into Alpha ISA to use your allowance. No investments are being sold.",
        "Sell the joint GIA entirely; split proceeds equally across both ISAs, subject to confirmation of remaining allowances.",
    )
    report = report.replace(
        "## Fees & Charges",
        "## Tax Implications\nThe disposal may create a capital gains tax liability assessed against the annual exempt amount. [REVIEW: Confirm capital gains tax.]\n## Fees & Charges",
    )
    assert score(report, case)["passed"]
    assert not score(report.replace("entirely", "in part"), case)["passed"]
    assert not score(
        report.replace(
            "subject to confirmation of remaining allowances", "immediately"
        ),
        case,
    )["passed"]


def test_fingerprint_detects_expectation_and_source_edits(tmp_path):
    e = evaluation
    source = tmp_path / "source.txt"
    source.write_text("one")
    first = e.fingerprint_files([source])
    source.write_text("two")
    assert first != e.fingerprint_files([source])
    assert e.fingerprint({"a": 1}) != e.fingerprint({"a": 2})


def test_summary_keeps_failed_run_usage_without_cost_tracking():
    result = evaluation.summarise_runs(
        [
            {
                "passed": False,
                "usage": [
                    {
                        "input_tokens": 50,
                        "output_tokens": 5,
                        "outcome": "failed",
                    }
                ],
                "latency_seconds": 2,
            }
        ]
    )
    assert result == {
        "runs": 1,
        "valid_drafts": 0,
        "input_tokens": 50,
        "output_tokens": 5,
        "retries": 0,
        "latency_seconds": 2,
        "pass_rate": 0,
    }


def test_source_reviewed_development_expectations_exist():
    files = sorted(Path("eval/development").glob("*.json"))
    assert len(files) == 4
    for path in files:
        expected = json.loads(path.read_text())
        assert expected["source_review"]
        assert expected["accounts"] and expected["actions"]


def test_extraction_checked_independently_of_rendered_report(report, case):
    manifest = {
        "status": "needs_review",
        "validation": {"passed": True},
        "facts": {
            "accounts": [
                {
                    "account_id": "A-ISA",
                    "owners": ["Alex Reed"],
                    "valuation": {
                        "value": "52000.00",
                        "precision": "exact",
                        "effective_date": "2026-04-30",
                    },
                }
            ],
            "requested_account_ids": ["A-ISA"],
            "actions": [
                {
                    "kind": "transfer",
                    "source_account_id": "C-CASH",
                    "destination_account_ids": ["A-ISA"],
                    "amount": {"value": "20000"},
                    "status": "agreed",
                }
            ],
        },
    }
    assert evaluation.score_report(report, manifest, case)["passed"]
    manifest["facts"]["accounts"][0]["valuation"]["value"] = "520000"
    assert not evaluation.score_report(report, manifest, case)["passed"]


def test_nested_usage_includes_failed_attempts_and_retry_count():
    summary = evaluation.summarise_runs(
        [
            {
                "passed": True,
                "usage": [
                    {
                        "usage": {"input_tokens": 25, "output_tokens": 4},
                        "attempt": 1,
                    },
                    {"usage": {}, "attempt": 2},
                ],
                "latency_seconds": 1,
            }
        ]
    )
    assert summary["input_tokens"] == 25
    assert summary["output_tokens"] == 4
    assert summary["retries"] == 1


def test_same_initial_and_invented_platform_rate_still_fails(report, case):
    assert not score(
        report.replace(
            "Initial charge: 0%.",
            "Initial charge: 0%. Platform charge: 0%. [REVIEW: confirm platform.]",
        ),
        case,
    )["passed"]


def test_reserved_sources_have_separate_answers():
    index = json.loads(Path("eval/partitions.json").read_text())
    assert len(index["reserved_families"]) >= 2
    for scenario in index["reserved_families"]:
        source = Path(scenario["sources"])
        assert source.is_dir()
        assert not list(source.rglob("*expected*"))
        expected = json.loads(Path(scenario["expectations"]).read_text())
        assert expected["partition"] == "reserved"
        assert scenario["family"] == expected["family"]


def test_failed_shared_generation_preserves_error_usage_and_snapshot(
    tmp_path, monkeypatch, case
):
    import agent_pipeline.generate as generation
    from agent_pipeline.contracts import Err, ProviderError

    source = tmp_path / "client"
    source.mkdir()
    (source / "notes.txt").write_text("Independent source")
    expected = tmp_path / "expected.json"
    expected.write_text(json.dumps(case))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"sections": []}))

    class Provider:
        records = [{"usage": {"input_tokens": 17}}]
        settings = {"model": "offline"}

    error = ProviderError("authentication", "Safe message", stage="extraction")
    calls = []

    def fail(**kwargs):
        calls.append(kwargs)
        return Err(error)

    monkeypatch.setattr(generation, "run_generation", fail)
    output = tmp_path / "output"
    result = evaluation.evaluate_case(
        client_dir=source,
        config_path=config,
        expected_path=expected,
        output_dir=output,
        provider=Provider(),
    )
    assert result["error"] == {
        "type": "ProviderError",
        "code": "authentication",
        "stage": "extraction",
    }
    assert result["usage"] == Provider.records
    assert len(calls) == 1 and "expected" not in calls[0]
    snapshot = Path(result["input_snapshot"])
    assert (snapshot / "expectations.json").is_file()
    package = Path(evaluation.__file__).parents[1]
    sources = {path.relative_to(package) for path in package.rglob("*.py")}
    copied = {
        path.relative_to(snapshot / "code")
        for path in (snapshot / "code").rglob("*.py")
    }
    assert copied == sources
    for relative in sources:
        assert (snapshot / "code" / relative).read_bytes() == (
            package / relative
        ).read_bytes()
    assert (
        Path(result["source_snapshot"]) / "notes.txt"
    ).read_text() == "Independent source"
    assert result["fingerprints"]["model_settings"]


def test_paraphrased_report_with_narrative_disposal_explanation(report, case):
    case["tax"] = True
    case["actions"] = [{"kind": "sell", "source": ["joint GIA"], "extent": "full"}]
    report = report.replace(
        "We recommend moving £20,000 from your cash account into Alpha ISA to use your allowance. No investments are being sold.",
        "Sell the joint GIA in full. This disposal will simplify your portfolio.",
    )
    report = report.replace(
        "## Fees & Charges",
        "## Tax Implications\nDisposal may create capital gains tax assessed against the annual exempt amount; confirm gains before implementation.\n## Fees & Charges",
    )
    assert score(report, case)["passed"]


def test_partial_sale_cannot_release_entire_valuation(report, case):
    case["actions"] = [{"kind": "sell", "source": ["A-ISA"], "extent": "partial"}]
    report = report.replace(
        "We recommend moving £20,000 from your cash account into Alpha ISA to use your allowance. No investments are being sold.",
        "Sell a portion of A-ISA, releasing £52,000.",
    )
    assert not score(report, case)["passed"]


def test_unstated_allocation_cannot_reuse_a_known_source_amount(report, case):
    case["actions"] = [{"kind": "contribute", "destination": ["Alpha ISA"]}]
    report = report.replace(
        "moving £20,000 from your cash account into Alpha ISA",
        "contributing £52,000 to Alpha ISA",
    )
    assert not score(report, case)["passed"]


def test_cli_loads_dotenv_and_stops_after_permanent_authentication_failure(
    tmp_path, monkeypatch
):
    import os

    import dotenv

    import agent_pipeline.adapters.providers as providers
    from agent_pipeline.contracts import Ok

    module = evaluation
    marker = "EVALUATION_TEST_CREDENTIAL"
    monkeypatch.delenv(marker, raising=False)
    env = tmp_path / ".env"
    env.write_text(marker + "=synthetic-not-a-real-secret")
    actual_load = dotenv.load_dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda: actual_load(env))
    calls = []

    def provider(**kwargs):
        assert os.getenv(marker) == "synthetic-not-a-real-secret"
        assert kwargs["model"] == "gpt-6-luna"
        return Ok(object())

    def evaluate(**kwargs):
        calls.append(kwargs)
        return {
            "passed": False,
            "score": 0,
            "error": {
                "type": "ProviderError",
                "code": "authentication",
                "stage": "provider",
            },
            "usage": [{"usage": {}}],
            "failures": ["authentication"],
        }

    monkeypatch.setattr(providers, "create_provider", provider)
    monkeypatch.setattr(module, "evaluate_case", evaluate)
    result = module.main(
        ["--clients", "one", "two", "--output-dir", str(tmp_path / "runs")]
    )
    assert result == 1
    assert len(calls) == 1
    summary = json.loads(
        next((tmp_path / "runs").glob("*/comparison.json")).read_text()
    )
    assert summary["variants"]["candidate"]["input_tokens"] == 0
    assert summary["variants"]["candidate"]["valid_drafts"] == 0
    assert summary["stopping_reason"] == "authentication"
