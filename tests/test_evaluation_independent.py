"""Independent acceptance checks: report content and experiment trust boundaries."""

import hashlib
import json
import os
import sys
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from agent_pipeline import evaluation, experiments
from agent_pipeline.contracts import (
    Err,
    EvidenceBlock,
    EvidenceBundle,
    ModelReply,
    Ok,
    ProviderError,
)


@pytest.fixture
def reviewed_case():
    # Independently checked against client 01's request, meeting, and custody data.
    expected = json.loads(Path("eval/development/client_01_clean.json").read_text())
    report = """# Investment Advice Report
## Introduction
This report covers Margaret Hughes's Holloway ISA.
This firm is authorised and regulated by the Financial Conduct Authority.
## Background & Objectives
You remain retired, seeking growth with moderate risk (risk profile 4) and no income requirement.
| Account | Owner | Type | Value |
| --- | --- | --- | --- |
| H-ISA-01 | Margaret Hughes | Stocks & Shares ISA | £52,000 as at 30 April 2026 |
## Recommendations
Transfer £20,000 from your cash account into your Stocks & Shares ISA to use the ISA allowance.
No investments are being sold. Confirm the relevant platform charge before implementation.
## Fees & Charges
The initial charge is 0%. Confirm the platform charge. Confirm the ongoing advice charge.
## Conclusion
The value of investments can fall as well as rise and you may get back less than you invest. Past performance is not a guide to future returns.
Please let us know if you wish to proceed.
"""
    return expected, report


def score(case, report):
    return evaluation.score_report(
        report, {"status": "needs_review", "validation": {"passed": True}}, case
    )


def test_source_reviewed_report_and_plain_paraphrase_pass(reviewed_case):
    expected, report = reviewed_case
    assert score(expected, report)["passed"]
    paraphrase = report.replace(
        "Transfer £20,000 from your cash account into your Stocks & Shares ISA",
        "Top up your ISA with GBP 20 thousand from your cash account",
    )
    assert score(expected, paraphrase)["passed"]


def test_sourced_initial_advice_charge_wording_is_valid(reviewed_case):
    expected, report = reviewed_case
    paraphrase = report.replace(
        "The initial charge is 0%.", "The initial advice charge is 0%."
    )
    assert score(expected, paraphrase)["passed"]


@pytest.mark.parametrize(
    "extra",
    [
        "Transfer £20,000 from your ISA into your cash account.",
        "You should transfer £20,000 from your ISA into your cash account.",
        "We recommend that you transfer £25,000 from your cash account into your ISA.",
        "Transfer £25,000 from your cash account into your ISA.",
        "Do not transfer £20,000 from your cash account into your ISA.",
        "Gift £20,000 to your grandchildren now.",
        "The platform's annual charge is 0.75%, subject to confirmation.",
    ],
)
def test_correct_action_does_not_hide_extra_unsupported_claim(reviewed_case, extra):
    expected, report = reviewed_case
    corrupt = report.replace("## Fees & Charges", extra + "\n## Fees & Charges")
    assert not score(expected, corrupt)["passed"], extra


def test_introduction_cannot_omit_scope_while_table_is_correct(reviewed_case):
    expected, report = reviewed_case
    report = report.replace(
        "This report covers Margaret Hughes's Holloway ISA.",
        "Thank you for attending our recent meeting.",
    )
    assert not score(expected, report)["passed"]


def test_no_extra_unscoped_disposal_even_when_one_sale_is_authorized(reviewed_case):
    expected, report = reviewed_case
    expected["actions"] = [{"kind": "sell", "source": ["H-ISA-01"], "extent": "full"}]
    report = report.replace(
        "Transfer £20,000 from your cash account into your Stocks & Shares ISA to use the ISA allowance.",
        "Sell H-ISA-01 in full. Sell the undisclosed pension in full.",
    ).replace("No investments are being sold. ", "")
    assert not score(expected, report)["passed"]


def test_reserved_family_cannot_enter_feedback_under_development_label():
    partitions = json.loads(Path("eval/partitions.json").read_text())
    reserved = partitions["reserved_families"][0]
    variant = {
        "client": "renamed_variant",
        "partition": "development",
        "family": reserved["family"],
    }
    with pytest.raises(ValueError, match="reserved|family"):
        experiments.development_rows([variant])


def test_evaluation_rerun_keeps_prior_input_snapshot(
    tmp_path, monkeypatch, reviewed_case
):
    import agent_pipeline.generate as generation

    source = tmp_path / "client"
    source.mkdir()
    notes = source / "notes.txt"
    notes.write_text("first source")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"sections": []}))
    expected = tmp_path / "expected.json"
    expected.write_text(json.dumps(reviewed_case[0]))
    provider = SimpleNamespace(
        settings={"model": "offline"},
        records=[{"usage": {"input_tokens": 12}, "estimated_cost_usd": None}],
    )
    calls = []

    def fail(**kwargs):
        calls.append(kwargs)
        return Err(ProviderError("authentication", "Unavailable", stage="extraction"))

    monkeypatch.setattr(generation, "run_generation", fail)

    def evaluate():
        return evaluation.evaluate_case(
            client_dir=source,
            config_path=config,
            expected_path=expected,
            output_dir=tmp_path / "evaluations",
            provider=provider,
        )

    first = evaluate()
    notes.write_text("second source")
    second = evaluate()
    assert first["evaluation_id"] != second["evaluation_id"]
    assert first["fingerprints"]["inputs"] != second["fingerprints"]["inputs"]
    assert len(calls) == 2
    assert all(call["provider"] is provider for call in calls)
    assert all("expected" not in key for call in calls for key in call)
    assert first["error"]["code"] == "authentication"
    assert first["usage"] == provider.records
    # Named immutable client directories preserve client identity during generation.
    assert (Path(first["source_snapshot"]) / "notes.txt").read_text() == "first source"
    assert (
        Path(second["source_snapshot"]) / "notes.txt"
    ).read_text() == "second source"


def test_changed_config_stops_before_any_optimizer_setup(tmp_path, monkeypatch):
    config = {"extraction_prompt": "Approved instructions", "sections": []}
    registration = {
        "config_sha256": evaluation.fingerprint(config),
        "prompts": {"extraction_prompt": "prompts:/extract/1"},
    }
    approval = {
        "review_status": "approved",
        "reviewed_by": "Reviewer",
        "reviewed_at": "2026-09-26T12:00:00Z",
        "config_sha256": registration["config_sha256"],
        "prompt_versions": registration["prompts"],
    }

    def forbid(*args, **kwargs):
        pytest.fail("Changed config reached optimizer setup")

    monkeypatch.setattr(experiments, "_local_mlflow", forbid)
    with pytest.raises(ValueError, match="changed"):
        experiments.optimize_reviewed(
            config={**config, "extraction_prompt": "Unreviewed replacement"},
            registration=registration,
            approval=approval,
            prompt_key="extraction_prompt",
            expectations=[],
            data_dir=tmp_path,
            expectations_dir=tmp_path,
            directory=tmp_path,
        )


def test_offline_optimizer_uses_shared_generation_and_never_promotes(
    tmp_path, monkeypatch, reviewed_case
):
    import agent_pipeline.generate as generation
    import agent_pipeline.providers as providers

    expected, report = reviewed_case
    config = json.loads(Path("config/template_config.json").read_text())
    key = "extraction_prompt"
    uri = "prompts:/reviewed_extraction/1"
    registration = {
        "config_sha256": evaluation.fingerprint(config),
        "prompts": {key: uri},
    }
    approval = {
        "review_status": "approved",
        "reviewed_by": "Offline fixture only",
        "reviewed_at": "2026-09-26T12:00:00Z",
        "config_sha256": registration["config_sha256"],
        "prompt_versions": registration["prompts"],
    }
    (tmp_path / expected["client"]).mkdir()
    (tmp_path / expected["client"] / "notes.txt").write_text("Offline input")
    (tmp_path / f"{expected['client']}.json").write_text(json.dumps(expected))
    calls = []
    usage = [
        {"usage": {"input_tokens": 19, "output_tokens": 8}, "estimated_cost_usd": 0.02}
    ]

    def generate(**kwargs):
        calls.append(kwargs)
        candidate = json.loads(kwargs["config_path"].read_text())
        assert candidate[key] == "Candidate prompt text"
        assert {k: v for k, v in candidate.items() if k != key} == {
            k: v for k, v in config.items() if k != key
        }
        path = kwargs["output_dir"] / "report.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report, encoding="utf-8")
        return Ok(
            {
                "status": "needs_review",
                "validation": {"passed": True},
                "output_path": str(path),
                "usage": usage,
            }
        )

    monkeypatch.setattr(generation, "run_generation", generate)
    monkeypatch.setattr(
        providers,
        "create_provider",
        lambda **kwargs: Ok(SimpleNamespace(settings=kwargs)),
    )
    optimize_module = ModuleType("mlflow.genai.optimize")
    monkeypatch.setattr(
        optimize_module, "GepaPromptOptimizer", lambda **kwargs: kwargs, raising=False
    )
    scorers_module = ModuleType("mlflow.genai.scorers")
    monkeypatch.setattr(
        scorers_module, "scorer", lambda function: function, raising=False
    )
    monkeypatch.setitem(sys.modules, "mlflow.genai.optimize", optimize_module)
    monkeypatch.setitem(sys.modules, "mlflow.genai.scorers", scorers_module)

    def optimize(**kwargs):
        assert kwargs["optimizer"]["max_metric_calls"] == 2
        assert kwargs["prompt_uris"] == [uri]
        output = kwargs["predict_fn"](expected["client"])
        assert output["passed"], output
        assert kwargs["scorers"][0](output) == 1
        assert kwargs["scorers"][0]({"passed": False, "score": 0.99}) == 0
        return SimpleNamespace(
            optimized_prompts=[
                SimpleNamespace(uri=uri, template="Candidate prompt text")
            ]
        )

    fake = SimpleNamespace(
        start_run=lambda **kwargs: nullcontext(),
        log_params=lambda *args: None,
        log_dict=lambda *args: None,
        genai=SimpleNamespace(
            load_prompt=lambda _: SimpleNamespace(template="Candidate prompt text"),
            optimize_prompts=optimize,
        ),
    )
    directory = tmp_path / "experiment"
    directory.mkdir()
    monkeypatch.setattr(experiments, "_local_mlflow", lambda _: fake)
    result = experiments.optimize_reviewed(
        config=config,
        registration=registration,
        approval=approval,
        prompt_key=key,
        expectations=[expected],
        data_dir=tmp_path,
        expectations_dir=tmp_path,
        directory=directory,
        max_metric_calls=2,
    )
    assert len(calls) == 1
    assert calls[0]["provider"].settings.get("cache_dir") is None
    assert result["promotion"] == "not_evaluated"
    assert result["review_status"] == "PENDING USER REVIEW"
    assert result["cost"]["generation"]["known_cost_usd"] == 0.02
    assert result["cost"]["generation"]["input_tokens"] == 19
    assert result["cost"]["optimizer_cost_usd"] is None
    assert result["cost"]["total_cost_per_valid_draft_usd"] is None
    assert (directory / "optimisation-ledger.json").is_file()


@pytest.fixture
def finalist_comparison(tmp_path, monkeypatch):
    """Exercise orchestration only; generation and judge adapters are offline doubles."""
    import agent_pipeline.evidence as evidence

    config = json.loads(Path("config/template_config.json").read_text())
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(config))
    finalist = tmp_path / "finalist.json"
    finalist.write_text(json.dumps({**config, "extraction_prompt": "Finalist prompt"}))
    registration = {
        "config_sha256": evaluation.fingerprint(config),
        "prompts": {"extraction_prompt": "prompts:/approved/1"},
    }
    approval = {
        "review_status": "approved",
        "reviewed_by": "Offline test",
        "reviewed_at": "2026-09-26T12:00:00Z",
        "config_sha256": registration["config_sha256"],
        "prompt_versions": registration["prompts"],
    }
    generation_calls, judge_calls, providers_created = [], [], []
    controls = {
        "hard_failure": False,
        "cached": False,
        "judge_failure": False,
        "judge_concerns": False,
        "changed_settings": False,
        "changed_sources": False,
        "changed_config_fingerprint": False,
    }

    def provider_factory():
        model = (
            "different-model"
            if controls["changed_settings"] and providers_created
            else "gpt-6-luna"
        )
        provider = SimpleNamespace(
            settings={"model": model, "temperature": 0}, records=[]
        )
        providers_created.append(provider)
        return Ok(provider)

    def fake_evaluate(**kwargs):
        generation_calls.append(kwargs)
        # Configuration snapshots can move; identify the variant by captured content.
        consumed_config = json.loads(kwargs["config_path"].read_text())
        is_finalist = consumed_config["extraction_prompt"] == "Finalist prompt"
        path = kwargs["output_dir"] / "report.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("FINALIST REPORT" if is_finalist else "BASELINE REPORT")
        frozen_sources = kwargs["output_dir"] / "sources" / kwargs["client_dir"].name
        frozen_sources.mkdir(parents=True)
        source_bytes = b"Independent client source fact"
        if controls["changed_sources"] and is_finalist:
            source_bytes += b" changed"
        (frozen_sources / "independent.txt").write_bytes(source_bytes)
        passed = not (controls["hard_failure"] and is_finalist)
        usage = [
            {
                "usage": {"input_tokens": 10},
                "estimated_cost_usd": 0.2,
                "outcome": "cache_hit" if controls["cached"] else "success",
            }
        ]
        return {
            "evaluation_id": str(len(generation_calls)),
            "client": kwargs["client_dir"].name,
            "passed": passed,
            "score": 1.0 if passed else 0.8,
            "failures": [] if passed else ["Wrong transfer"],
            "usage": usage,
            "latency_seconds": 1.0,
            "manifest": {"output_path": str(path)},
            "fingerprints": {
                "model_settings": evaluation.fingerprint(kwargs["provider"].settings),
                "inputs": evaluation.fingerprint(
                    [("independent.txt", hashlib.sha256(source_bytes).hexdigest())]
                ),
                "expectations": kwargs["client_dir"].name,
                "code": "same-code",
                "scorer": "same-scorer",
                "config": "unexpected-config"
                if controls["changed_config_fingerprint"]
                else evaluation.fingerprint(consumed_config),
            },
            "source_snapshot": str(frozen_sources),
        }

    def load_independent_sources(*args, **kwargs):
        location = args[0] if args else kwargs["client_dir"]
        assert Path(location).parent.name == "sources", (
            "Judge reread mutable original sources"
        )
        return Ok(
            EvidenceBundle(
                blocks=[
                    EvidenceBlock(
                        "source-1",
                        "independent.txt",
                        "line:1",
                        "Independent client source fact",
                        "source-hash",
                    )
                ],
                inventory=[],
                databases=[],
            )
        )

    class Judge:
        settings = {"model": "gpt-6-luna"}

        def __init__(self):
            self.records = []

        def complete(self, **kwargs):
            judge_calls.append(kwargs)
            self.records.append(
                {
                    "usage": {"input_tokens": 7, "output_tokens": 3},
                    "estimated_cost_usd": 0.1,
                }
            )
            if controls["judge_failure"]:
                return Err(
                    ProviderError(
                        "unavailable", "Offline failure", stage="advisory_judge"
                    )
                )
            context = json.dumps(kwargs["context"])
            assert "Independent client source fact" in context
            return Ok(
                ModelReply(
                    text="",
                    data={
                        "score": 0.9 if "FINALIST REPORT" in context else 0.6,
                        "rationale": "Offline quality comparison",
                        "concerns": ["Unresolved source-support concern"]
                        if controls["judge_concerns"]
                        else [],
                    },
                    usage={"input_tokens": 7, "output_tokens": 3},
                    model="gpt-6-luna",
                )
            )

    monkeypatch.setattr(evaluation, "evaluate_case", fake_evaluate)
    monkeypatch.setattr(evidence, "load_sources", load_independent_sources)
    prompt = "Judge factual support and clarity against independent source evidence."
    rubric = {
        "review_status": "approved",
        "reviewed_by": "Offline test",
        "reviewed_at": "2026-09-26T12:00:00Z",
        "instructions": "Require complete supported recommendations.",
        "prompt_sha256": evaluation.fingerprint(prompt),
    }
    arguments = {
        "baseline_config_path": baseline,
        "finalist_config_path": finalist,
        "registration": registration,
        "approval": approval,
        "data_dir": Path("data"),
        "expectations_dir": Path("eval/development"),
        "output_dir": tmp_path / "comparison",
        "provider_factory": provider_factory,
        "judge_provider_factory": lambda: Ok(Judge()),
        "judge_prompt": prompt,
        "rubric": rubric,
        "optimizer_cost_usd": 0.4,
    }
    return arguments, generation_calls, judge_calls, providers_created, controls


def test_finalist_comparison_uses_two_fresh_generations_for_all_cases(
    finalist_comparison,
):
    arguments, calls, judges, providers, _ = finalist_comparison
    result = getattr(evaluation, "compare_finalist")(**arguments)
    assert (
        len(calls) == len(providers) == 24
    )  # 2 variants × 2 repetitions × (4 + 2 cases).
    assert len({str(call["output_dir"]) for call in calls}) == 24
    assert len(judges) == 24
    assert all(call["task"] == "advisory_judge" for call in judges)
    assert sum(run["partition"] == "reserved" for run in result["runs"]) == 8
    assert result["recommendation"] == "promote_candidate"
    assert result["cost"]["total_cost_complete"] is True
    assert result["cost"]["total_known_cost_usd"] == pytest.approx(7.6)
    assert not result["gaps"]


@pytest.mark.parametrize(
    "failure",
    [
        "hard_failure",
        "cached",
        "judge_failure",
        "judge_concerns",
        "changed_settings",
        "changed_sources",
        "changed_config_fingerprint",
    ],
)
def test_finalist_comparison_never_promotes_incomplete_or_incorrect_trials(
    finalist_comparison, failure
):
    arguments, _, _, _, controls = finalist_comparison
    controls[failure] = True
    result = getattr(evaluation, "compare_finalist")(**arguments)
    assert result["recommendation"] != "promote_candidate"


def test_finalist_comparison_requires_review_before_provider_setup(finalist_comparison):
    arguments, calls, _, providers, _ = finalist_comparison
    arguments["approval"] = {}
    with pytest.raises(ValueError, match="review"):
        getattr(evaluation, "compare_finalist")(**arguments)
    assert calls == providers == []


def test_finalist_comparison_freezes_config_before_provider_setup(
    finalist_comparison, monkeypatch
):
    arguments, calls, _, _, _ = finalist_comparison
    finalist_path = arguments["finalist_config_path"]
    reviewed_finalist = json.loads(finalist_path.read_text())
    provider_factory = arguments["provider_factory"]
    evaluate = evaluation.evaluate_case

    def mutate_original_then_create_provider():
        finalist_path.write_text(
            json.dumps(
                {**reviewed_finalist, "document_title": "Unreviewed replacement title"}
            )
        )
        return provider_factory()

    def inspect_consumed_config(**kwargs):
        consumed = json.loads(kwargs["config_path"].read_text())
        assert consumed["document_title"] == reviewed_finalist["document_title"], (
            "Generation consumed unreviewed configuration"
        )
        return evaluate(**kwargs)

    arguments["provider_factory"] = mutate_original_then_create_provider
    monkeypatch.setattr(evaluation, "evaluate_case", inspect_consumed_config)
    result = getattr(evaluation, "compare_finalist")(**arguments)
    assert len(calls) == 24
    assert result["recommendation"] == "promote_candidate"
    assert not result["gaps"]


def test_missing_judge_remains_explicit_gap(finalist_comparison):
    arguments, calls, judges, _, _ = finalist_comparison
    arguments["judge_provider_factory"] = None
    result = getattr(evaluation, "compare_finalist")(**arguments)
    assert len(calls) == 24 and not judges
    assert result["recommendation"] != "promote_candidate"
    assert result["gaps"]


def test_missing_development_case_cannot_shrink_required_comparison(
    finalist_comparison, tmp_path
):
    arguments, _, _, _, _ = finalist_comparison
    incomplete = tmp_path / "incomplete-expectations"
    incomplete.mkdir()
    for source in sorted(Path("eval/development").glob("*.json"))[:-1]:
        (incomplete / source.name).write_bytes(source.read_bytes())
    arguments["expectations_dir"] = incomplete
    try:
        result = getattr(evaluation, "compare_finalist")(**arguments)
    except ValueError:
        return
    assert result["recommendation"] != "promote_candidate"
    assert result["gaps"]


@pytest.mark.parametrize(
    "extra",
    [
        "",
        "The reserved loan funds of £130,000 are also available for immediate investment.",
        "The earnout of £210,000 is available for immediate investment.",
    ],
)
def test_funding_status_matches_independent_source_even_with_correct_figures_elsewhere(
    extra,
):
    expected = json.loads(
        Path("eval/reserved/reserved_commitments_partial.json").read_text()
    )
    report = f"""# Report
## Introduction
We cover Owen Webb and Maya Webb's QZ-GROW-4 joint GIA and QZ-BOND-9 joint bond.
{evaluation.FCA}
## Background & Objectives
You seek balanced growth to replace income in four years.
| Account | Owner | Type | Value |
| --- | --- | --- | --- |
| QZ-GROW-4 | Owen Webb and Maya Webb | GIA | £146,700 as at 8 February 2027 |
| QZ-BOND-9 | Owen Webb and Maya Webb | Bond | £112,800 as at 8 February 2027 |
## Recommendations
Sell a portion of QZ-GROW-4. Contribute to QZ-GROW-4, with the allocation to be confirmed. Retain QZ-BOND-9 unchanged.
The completion receipt is £570,000. After reserving £130,000 for the loan, available completion funds are £440,000. The contingent earnout of £210,000 is excluded from current funding. {extra} Confirm loan timing.
## Tax Implications
The disposal may create capital gains tax assessed against the annual exempt amount. Confirm capital gains tax before implementation.
## Fees & Charges
The initial charge is 0.4%. Confirm the platform charge. Confirm the ongoing advice charge.
## Conclusion
{evaluation.RISK}
Please let us know if you wish to proceed.
"""
    assert score(expected, report)["passed"] is (not extra)


def test_evaluation_snapshot_rejects_cross_client_hardlink_before_copy(
    tmp_path, monkeypatch, reviewed_case
):
    import agent_pipeline.generate as generation

    other = tmp_path / "other-client.txt"
    other.write_text("PRIVATE OTHER CLIENT EVIDENCE")
    source = tmp_path / "client"
    source.mkdir()
    os.link(other, source / "linked.txt")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"sections": []}))
    expected = tmp_path / "expected.json"
    expected.write_text(json.dumps(reviewed_case[0]))
    output = tmp_path / "evaluation"
    calls = []

    def generate(**kwargs):
        calls.append(kwargs)
        return Err(ProviderError("offline", "Offline failure"))

    monkeypatch.setattr(generation, "run_generation", generate)
    try:
        result = evaluation.evaluate_case(
            client_dir=source,
            config_path=config,
            expected_path=expected,
            output_dir=output,
            provider=SimpleNamespace(settings={}, records=[]),
        )
    except ValueError:
        pass
    else:
        assert not result["passed"]
    assert not calls, "Unsafe input reached shared generation"
    assert not any(
        b"PRIVATE OTHER CLIENT EVIDENCE" in p.read_bytes()
        for p in output.rglob("*")
        if p.is_file()
    )


def test_generation_consumes_frozen_snapshot_when_original_changes(
    tmp_path, monkeypatch, reviewed_case
):
    import agent_pipeline.generate as generation

    source = tmp_path / "original-client"
    source.mkdir()
    notes = source / "notes.txt"
    notes.write_text("Evidence at fingerprint time")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"sections": []}))
    expected = tmp_path / "expected.json"
    expected.write_text(json.dumps(reviewed_case[0]))
    observed = []

    def generate(**kwargs):
        notes.write_text("Later replacement evidence")
        observed.append(
            (
                kwargs["client_dir"].name,
                (kwargs["client_dir"] / "notes.txt").read_text(),
            )
        )
        return Err(ProviderError("offline", "Offline failure"))

    monkeypatch.setattr(generation, "run_generation", generate)
    result = evaluation.evaluate_case(
        client_dir=source,
        config_path=config,
        expected_path=expected,
        output_dir=tmp_path / "evaluation",
        provider=SimpleNamespace(settings={}, records=[]),
    )
    assert observed == [("original-client", "Evidence at fingerprint time")]
    assert result["client"] == "original-client"
