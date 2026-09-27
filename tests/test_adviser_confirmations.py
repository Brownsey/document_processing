import json
from copy import deepcopy

import pytest
from test_workflow import CaseProvider, configured_case

from agent_pipeline import evaluation, generate
from agent_pipeline.contracts import Err, ModelReply, Ok
from agent_pipeline.domain import ReviewItem
from agent_pipeline.rendering import render_slot
from agent_pipeline.workflow import run_generation, validate_config


def review_facts():
    return {
        "actions": [{"kind": "retain", "source_account_id": "GIA"}],
        "accounts": [{"account_id": "GIA", "account_type": "GIA"}],
        "review_items": [
            {"code": "missing", "message": "Confirm the missing balance."},
            {
                "code": "different_values",
                "category": "discrepancy",
                "message": "Two records give different values for the same date.",
            },
            {
                "code": "isa_funding_conflict",
                "message": "Resolve the sale and ISA capacity mismatch before implementation.",
            },
        ],
    }


def test_discrepancy_filters_queries_without_losing_warning_or_audit_facts():
    facts = review_facts()
    original = deepcopy(facts)
    full = render_slot("adviser_queries", facts, {})
    assert "missing balance" in full and "different values" in full
    config = {"adviser_confirmations": "discrepancy"}
    for renderer in ("adviser_queries", "actions"):
        text = render_slot(renderer, facts, config)
        assert "missing balance" not in text
        assert "different values" in text
    plan = render_slot("action_plan", facts, config)
    assert plan.index("IMPLEMENTATION PAUSED") < plan.index("- Retain")
    assert "ISA capacity mismatch" in plan
    assert facts == original
    for renderer in ("fees", "tax", "holdings"):
        assert render_slot(renderer, facts, config) == render_slot(renderer, facts, {})


def test_discrepancy_with_only_routine_queries_has_no_empty_heading():
    facts = {"review_items": [{"code": "missing", "message": "Confirm balance."}]}
    assert (
        render_slot("adviser_queries", facts, {"adviser_confirmations": "discrepancy"})
        == ""
    )


@pytest.mark.parametrize("mode", ["full", "discrepancy"])
def test_nonblocking_conflicts_remain_visible_without_duplicate_queries(mode):
    conflict = {
        "code": "source_difference",
        "message": "The two records give different dates.",
        "blocking": False,
    }
    facts = {
        "conflicts": [conflict],
        "review_items": [conflict | {"category": "discrepancy"}],
    }
    original = deepcopy(facts)
    config = {"adviser_confirmations": mode}
    assert (
        render_slot("adviser_queries", {"conflicts": [conflict]}, config).count(
            "different dates"
        )
        == 1
    )
    assert render_slot("adviser_queries", facts, config).count("different dates") == 1
    assert facts == original


def test_typed_review_category_keeps_old_facts_valid_and_rejects_unknown_values():
    assert (
        ReviewItem(code="missing", message="Confirm balance.").category
        == "confirmation"
    )
    assert (
        ReviewItem(
            code="different", message="Conflicting dates.", category="discrepancy"
        ).category
        == "discrepancy"
    )
    with pytest.raises(ValueError):
        ReviewItem.model_validate(
            {"code": "missing", "message": "Confirm balance.", "category": "anything"}
        )


def test_invalid_confirmation_mode_is_rejected():
    config = json.loads(open("config/template_config.json", encoding="utf-8").read())
    assert isinstance(validate_config(config | {"adviser_confirmations": "off"}), Err)


class QueryProvider(CaseProvider):
    def complete(self, **request):
        result = super().complete(**request)
        if request["task"] == "extract":
            data = result.value.data
            data["review_items"] = [
                {"code": "missing", "message": "Confirm the missing rationale."},
                {
                    "code": "different",
                    "message": "Reconcile differing instructions.",
                    "category": "discrepancy",
                },
            ]
            return Ok(ModelReply("", data, {}, "fake"))
        return result


@pytest.mark.parametrize("mode", ["full", "discrepancy"])
def test_generation_filters_report_only_and_tells_support_reviewer(tmp_path, mode):
    client, config_path, output, config = configured_case(tmp_path)
    section = config["sections"][-1]
    section["template"] = "<<actions>>\n\n<<queries>>"
    section["placeholders"]["actions"]["renderer"] = "action_plan"
    section["placeholders"]["queries"] = {
        "renderer": "adviser_queries",
        "output_type": "static",
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")
    provider = QueryProvider()
    result = run_generation(
        client_dir=client,
        config_path=config_path,
        output_dir=output,
        provider=provider,
        adviser_confirmations=mode,
    )
    assert isinstance(result, Ok), result
    report = (output / "client.md").read_text(encoding="utf-8")
    assert ("missing rationale" in report) == (mode == "full")
    assert "differing instructions" in report
    manifest = json.loads((output / "client.manifest.json").read_text())
    assert any(
        "missing rationale" in item["message"] for item in manifest["review_items"]
    )
    assert manifest["adviser_confirmations"] == mode
    review = next(c for c in provider.calls if c["task"] == "validate_support")
    assert review["context"]["adviser_confirmations"] == mode


@pytest.mark.parametrize("command", [generate, evaluation])
def test_cli_exposes_mode_and_rejects_unknown_mode(command, capsys):
    with pytest.raises(SystemExit) as help_exit:
        command.main(["--help"])
    assert help_exit.value.code == 0
    assert "--adviser-confirmations {full,discrepancy}" in capsys.readouterr().out
    with pytest.raises(SystemExit) as bad_exit:
        command.main(["--adviser-confirmations", "off"])
    assert bad_exit.value.code == 2
