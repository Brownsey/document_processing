"""Independent end-to-end offline checks of the adviser-query display setting."""

import json
from copy import deepcopy

import pytest
from test_adviser_confirmations import QueryProvider
from test_workflow import configured_case

from agent_pipeline import evaluation, generate, providers
from agent_pipeline.contracts import Err, Ok
from agent_pipeline.rendering import render_slot
from agent_pipeline.workflow import run_generation


@pytest.mark.parametrize("command", [generate, evaluation])
@pytest.mark.parametrize(
    "configured,override,effective",
    [
        (None, None, "full"),
        ("discrepancy", None, "discrepancy"),
        ("discrepancy", "full", "full"),
        ("full", "discrepancy", "discrepancy"),
    ],
)
def test_both_clis_apply_mode_and_persist_effective_configuration(
    tmp_path, monkeypatch, command, configured, override, effective
):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    client, config_path, output, config = configured_case(tmp_path)
    if configured is not None:
        config["adviser_confirmations"] = configured
    config_path.write_text(json.dumps(config))
    provider = QueryProvider()
    monkeypatch.setattr(generate, "create_provider", lambda **kwargs: Ok(provider))
    monkeypatch.setattr(providers, "create_provider", lambda **kwargs: Ok(provider))
    args = [
        "--data-dir",
        str(client.parent),
        "--config",
        str(config_path),
        "--output-dir",
        str(output),
    ]
    if command is evaluation:
        expected = {"accounts": [], "actions": [], "tax": False, "initial_rate": "0"}
        (tmp_path / "client.json").write_text(json.dumps(expected))
        args += ["--clients", "client", "--expectations-dir", str(tmp_path)]
    else:
        args += ["--client", "client"]
    if override is not None:
        args += ["--adviser-confirmations", override]
    # This synthetic report deliberately lacks the full evaluation report contract.
    assert command.main(args) == (1 if command is evaluation else 0)
    manifest = json.loads(next(output.rglob("client.manifest.json")).read_text())
    assert manifest["status"] == "needs_review"
    assert manifest["adviser_confirmations"] == effective
    report = next(output.rglob("client.md")).read_text()
    assert ("missing rationale" in report) == (effective == "full")
    assert "differing instructions" in report
    assert {item["code"] for item in manifest["review_items"]} >= {
        "missing",
        "different",
    }
    review = next(c for c in provider.calls if c["task"] == "validate_support")
    assert review["context"]["adviser_confirmations"] == effective
    if command is evaluation:
        saved = json.loads(next(output.rglob("snapshot/config.json")).read_text())
        assert saved.get("adviser_confirmations", "full") == effective
        result = json.loads(next(output.rglob("evaluation-*.json")).read_text())
        assert result["fingerprints"]["config"] == evaluation.fingerprint(saved)
        assert result["manifest"]["adviser_confirmations"] == effective


@pytest.mark.parametrize("mode", ["off", "FULL", "", None, []])
def test_invalid_config_mode_blocks_before_provider_and_leaves_no_report(
    tmp_path, mode
):
    client, config_path, output, config = configured_case(tmp_path)
    config["adviser_confirmations"] = mode
    config_path.write_text(json.dumps(config))
    provider = QueryProvider()
    result = run_generation(
        client_dir=client, config_path=config_path, output_dir=output, provider=provider
    )
    assert isinstance(result, Err) and result.error.code == "invalid_config"
    assert not provider.calls
    assert not (output / "client.md").exists()


def test_display_filter_preserves_conditions_blockers_uncertainty_and_fee_tax_checks():
    facts = {
        "accounts": [{"account_id": "GIA", "account_type": "GIA", "owners": ["Alex"]}],
        "requested_account_ids": ["GIA"],
        "actions": [
            {
                "kind": "dispose",
                "source_account_id": "GIA",
                "extent": "full",
                "status": "conditional",
                "conditions": ["Obtain client approval"],
            }
        ],
        "review_items": [
            {"code": "routine", "message": "Confirm the missing detail."},
            {
                "code": "blocked",
                "message": "Resolve conflicting instructions.",
                "blocking": True,
            },
        ],
    }
    original = deepcopy(facts)
    config = {"adviser_confirmations": "discrepancy"}
    for renderer in ["actions", "action_plan"]:
        text = render_slot(renderer, facts, config)
        assert "Obtain client approval" in text and "Do not implement" in text
    queries = render_slot("adviser_queries", facts, config)
    assert "missing detail" not in queries
    assert "Resolve conflicting instructions" in queries
    for renderer in ["holdings", "fees", "tax"]:
        assert render_slot(renderer, facts, config) == render_slot(renderer, facts, {})
    assert "Value not supplied" in render_slot("holdings", facts, config)
    assert facts == original
