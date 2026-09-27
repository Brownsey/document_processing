"""Independent checks of report content and evaluation input isolation."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_pipeline.contracts import (
    Err,
    ProviderError,
)
from agent_pipeline.evaluation import runner as evaluation
from agent_pipeline.evaluation import structure


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
        records=[{"usage": {"input_tokens": 12}}],
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
{structure.FCA}
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
{structure.RISK}
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
