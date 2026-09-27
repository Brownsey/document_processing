"""Independent scoring must fail corrupt prose even when its manifest claims success."""

import copy
import importlib
import importlib.util
import json
from pathlib import Path

import pytest


def evaluator():
    spec = importlib.util.find_spec("agent_pipeline.evaluation")
    assert spec is not None, "independent evaluator is not implemented"
    return importlib.import_module("agent_pipeline.evaluation")


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
    return evaluator().score_report(
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
    e = evaluator()
    source = tmp_path / "source.txt"
    source.write_text("one")
    first = e.fingerprint_files([source])
    source.write_text("two")
    assert first != e.fingerprint_files([source])
    assert e.fingerprint({"a": 1}) != e.fingerprint({"a": 2})


def test_no_valid_draft_has_no_cost_denominator():
    result = evaluator().summarise_runs(
        [
            {
                "passed": False,
                "usage": [
                    {
                        "cost_usd": 0.4,
                        "input_tokens": 50,
                        "output_tokens": 5,
                        "outcome": "failed",
                    }
                ],
                "latency_seconds": 2,
            }
        ]
    )
    assert result["cost_per_valid_draft_usd"] is None
    assert result["known_cost_usd"] == 0.4
    assert result["input_tokens"] == 50
    assert result["valid_drafts"] == 0


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
    assert evaluator().score_report(report, manifest, case)["passed"]
    manifest["facts"]["accounts"][0]["valuation"]["value"] = "520000"
    assert not evaluator().score_report(report, manifest, case)["passed"]


def test_nested_usage_unknown_failure_cost_and_retry_count():
    summary = evaluator().summarise_runs(
        [
            {
                "passed": True,
                "usage": [
                    {
                        "usage": {"input_tokens": 25, "output_tokens": 4},
                        "estimated_cost_usd": 0.1,
                        "attempt": 1,
                    },
                    {"usage": {}, "estimated_cost_usd": None, "attempt": 2},
                ],
                "latency_seconds": 1,
            }
        ]
    )
    assert summary["input_tokens"] == 25
    assert summary["output_tokens"] == 4
    assert summary["cost_per_valid_draft_usd"] is None
    assert summary["unknown_cost_records"] == 1
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
        records = [{"usage": {"input_tokens": 17}, "estimated_cost_usd": None}]
        settings = {"model": "offline"}

    error = ProviderError("authentication", "Safe message", stage="extraction")
    calls = []

    def fail(**kwargs):
        calls.append(kwargs)
        return Err(error)

    monkeypatch.setattr(generation, "run_generation", fail)
    output = tmp_path / "output"
    result = evaluator().evaluate_case(
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

    import agent_pipeline.providers as providers
    from agent_pipeline.contracts import Ok

    module = evaluator()
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
            "usage": [{"estimated_cost_usd": None, "usage": {}}],
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
    assert summary["variants"]["candidate"]["unknown_cost_records"] == 1
    assert summary["stopping_reason"] == "authentication"


def test_advisory_judge_requires_reviewed_rubric_before_calls(tmp_path):
    module = evaluator()
    assert hasattr(module, "advisory_judge"), "postreview advisory judge is missing"

    class Never:
        def complete(self, **kwargs):
            raise AssertionError("unreviewed rubric called provider")

    result = module.advisory_judge(
        report="report",
        client_dir=tmp_path,
        expected={},
        provider=Never(),
        prompt="judge",
        rubric={},
    )
    assert result["status"] == "unavailable"


def test_advisory_judge_reads_independent_sources_and_meters(tmp_path, monkeypatch):
    module = evaluator()
    assert hasattr(module, "advisory_judge"), "postreview advisory judge is missing"
    import agent_pipeline.evidence as evidence
    from agent_pipeline.contracts import EvidenceBlock, EvidenceBundle, ModelReply, Ok

    monkeypatch.setattr(
        evidence,
        "load_sources",
        lambda *args, **kwargs: Ok(
            EvidenceBundle(
                [
                    EvidenceBlock(
                        "e1",
                        "notes",
                        "paragraph 1",
                        "Independently sourced instruction",
                        "digest",
                    )
                ],
                [],
                [],
            )
        ),
    )

    class Judge:
        records = []
        settings = {"model": "offline-judge"}

        def complete(self, **kwargs):
            assert kwargs["task"] == "advisory_judge"
            assert kwargs["context"]["report"] == "actual rendered report"
            assert (
                kwargs["context"]["evidence"][0]["text"]
                == "Independently sourced instruction"
            )
            assert kwargs["context"]["expectations"]["partition"] == "reserved"
            assert "facts" not in kwargs["context"]
            self.records.append(
                {
                    "estimated_cost_usd": 0.03,
                    "usage": {"input_tokens": 27, "output_tokens": 4},
                }
            )
            return Ok(
                ModelReply(
                    "",
                    {
                        "score": 0.8,
                        "rationale": "Faithful, some repetition.",
                        "concerns": [],
                    },
                    {},
                    "offline-judge",
                )
            )

    prompt = "Judge only evidence-supported quality."
    rubric = {
        "review_status": "approved",
        "reviewed_by": "Test reviewer",
        "reviewed_at": "2026-09-26",
        "instructions": "Check clarity and uncertainty.",
        "prompt_sha256": module.fingerprint(prompt),
    }
    result = module.advisory_judge(
        report="actual rendered report",
        client_dir=tmp_path,
        expected={"partition": "reserved"},
        provider=Judge(),
        prompt=prompt,
        rubric=rubric,
    )
    assert result["status"] == "scored" and result["score"] == 0.8
    assert result["usage"][0]["estimated_cost_usd"] == 0.03


def test_postreview_comparison_gate_precedes_provider_setup(tmp_path):
    module = evaluator()
    assert hasattr(module, "compare_finalist"), "postreview comparison is missing"

    def never():
        raise AssertionError("provider setup reached before approval")

    with pytest.raises(ValueError, match="review"):
        module.compare_finalist(
            baseline_config_path=tmp_path / "baseline.json",
            finalist_config_path=tmp_path / "finalist.json",
            registration={},
            approval={},
            data_dir=tmp_path,
            expectations_dir=tmp_path,
            output_dir=tmp_path / "out",
            provider_factory=never,
        )


def test_pending_judge_rubric_rejected_before_generations(tmp_path):
    module = evaluator()
    config = tmp_path / "config.json"
    config.write_text("{}")
    registration = {
        "config_sha256": module.fingerprint({}),
        "prompts": {"extraction_prompt": "prompts:/reviewed/1"},
    }
    approval = {
        "review_status": "approved",
        "reviewed_by": "Reviewer",
        "reviewed_at": "2026-09-26",
        "config_sha256": registration["config_sha256"],
        "prompt_versions": registration["prompts"],
    }

    def never():
        raise AssertionError("Provider reached before judge rubric validation")

    with pytest.raises(ValueError, match="rubric"):
        module.compare_finalist(
            baseline_config_path=config,
            finalist_config_path=config,
            registration=registration,
            approval=approval,
            data_dir=tmp_path,
            expectations_dir=tmp_path,
            output_dir=tmp_path / "out",
            provider_factory=never,
            judge_provider_factory=never,
            judge_prompt="judge",
            rubric={"review_status": "pending_user_review"},
        )
