"""Independent publication-override checks through the real offline workflow."""

import argparse
import json

import pytest
from test_workflow import CaseProvider, configured_case

from agent_pipeline import generate
from agent_pipeline.cli import add_runtime_arguments, workflow_options
from agent_pipeline.contracts import Err, ModelReply, Ok, ProviderError
from agent_pipeline.evaluation import score_report
from agent_pipeline.workflow import run_generation


class RejectedDraftProvider(CaseProvider):
    def __init__(self, failure=None, after_rejection=False):
        super().__init__()
        self.failure = failure
        self.after_rejection = after_rejection
        self.reviews = 0
        self.approved = []

    def approve_cache(self, reply):
        self.approved.append(reply)

    def complete(self, **request):
        result = super().complete(**request)
        active = not self.after_rejection or self.reviews > 0
        if active and request["task"] == "extract" and self.failure == "scope":
            result.value.data["requested_account_ids"] = []
        if active and request["task"] == "write":
            if self.failure == "provider":
                return Err(
                    ProviderError("unavailable", "Offline provider failure", "write")
                )
            if self.failure == "shape":
                return Ok(ModelReply("## Invalid nested heading", None, {}, "fake"))
        if request["task"] == "validate_support":
            self.reviews += 1
            if active and self.failure == "invalid_review":
                return Ok(ModelReply("", {"supported": "invalid"}, {}, "fake"))
            return Ok(
                ModelReply(
                    "",
                    {
                        "supported": False,
                        "issues": [f"Unsupported claim on draft {self.reviews}"],
                        "issue_kind": "facts",
                    },
                    {},
                    "fake",
                )
            )
        return result


@pytest.mark.parametrize("enabled", [False, True])
def test_generation_cli_override_is_explicit_and_preserves_rejection(
    tmp_path, monkeypatch, capsys, enabled
):
    client, config, output, _ = configured_case(tmp_path)
    provider = RejectedDraftProvider()
    monkeypatch.setattr(generate, "create_provider", lambda **kwargs: Ok(provider))
    monkeypatch.setattr(generate, "load_dotenv", lambda: None)
    args = [
        "--client",
        client.name,
        "--data-dir",
        str(client.parent),
        "--config",
        str(config),
        "--output-dir",
        str(output),
    ]
    if enabled:
        args.append("--publish-anyway")
    assert generate.main(args) == (0 if enabled else 1)
    manifest = json.loads((output / "client.manifest.json").read_text())
    assert manifest["publish_anyway"] is enabled
    assert manifest["published_anyway"] is enabled
    assert manifest["validation"]["passed"] is False
    assert provider.reviews == 3
    assert provider.approved == []
    assert manifest["error"]["details"]["issues"] == ["Unsupported claim on draft 3"]
    assert not any(key.startswith("_") for key in manifest)
    if enabled:
        report = (output / "client.md").read_text(encoding="utf-8")
        assert report.startswith("> **MANUAL CORRECTION REQUIRED")
        assert "Unsupported claim on draft 3" in report
        assert "Unsupported claim on draft 1" not in report
        assert "## Opening" in report and "## Next steps" in report
        assert manifest["status"] == "published_with_issues"
        assert "manual correction required" in capsys.readouterr().out
    else:
        assert not (output / "client.md").exists()


@pytest.mark.parametrize("failure", ["provider", "shape", "scope", "invalid_review"])
@pytest.mark.parametrize("after_rejection", [False, True])
def test_override_never_releases_failed_or_stale_draft(
    tmp_path, failure, after_rejection
):
    client, config, output, _ = configured_case(tmp_path)
    output.mkdir()
    (output / "client.md").write_text("Previous run must not survive")
    provider = RejectedDraftProvider(failure, after_rejection)
    result = run_generation(
        client_dir=client,
        config_path=config,
        output_dir=output,
        provider=provider,
        publish_anyway=True,
    )
    assert isinstance(result, Err), result
    assert not (output / "client.md").exists()
    manifest = json.loads((output / "client.manifest.json").read_text())
    assert manifest["published_anyway"] is False
    assert manifest["validation"]["passed"] is False
    assert "_blocked_report" not in manifest
    assert provider.approved == []


def test_invalid_input_stays_blocked_with_override(tmp_path):
    client, config, output, _ = configured_case(tmp_path)
    config.write_text("{}")
    provider = RejectedDraftProvider()
    result = run_generation(
        client_dir=client,
        config_path=config,
        output_dir=output,
        provider=provider,
        publish_anyway=True,
    )
    assert isinstance(result, Err)
    assert not provider.calls
    assert not (output / "client.md").exists()


def test_unclassified_rejection_cannot_bypass_repairs_and_publish(tmp_path):
    class UnclassifiedProvider(RejectedDraftProvider):
        def complete(self, **request):
            result = super().complete(**request)
            if request["task"] == "validate_support":
                result.value.data["issue_kind"] = "none"
            return result

    client, config, output, _ = configured_case(tmp_path)
    provider = UnclassifiedProvider()
    result = run_generation(
        client_dir=client,
        config_path=config,
        output_dir=output,
        provider=provider,
        publish_anyway=True,
    )
    assert isinstance(result, Err) or provider.reviews == 3


def test_override_flag_defaults_false_for_shared_cli():
    parser = argparse.ArgumentParser()
    add_runtime_arguments(parser)
    assert workflow_options(parser.parse_args([]))["publish_anyway"] is False
    assert (
        workflow_options(parser.parse_args(["--publish-anyway"]))["publish_anyway"]
        is True
    )


def test_valid_report_with_override_enabled_still_uses_normal_success(tmp_path):
    client, config, output, _ = configured_case(tmp_path)
    result = run_generation(
        client_dir=client,
        config_path=config,
        output_dir=output,
        provider=CaseProvider(),
        publish_anyway=True,
    )
    assert isinstance(result, Ok)
    assert result.value["status"] == "needs_review"
    assert result.value["published_anyway"] is False
    assert result.value["validation"]["passed"] is True
    assert "MANUAL CORRECTION REQUIRED" not in (output / "client.md").read_text()


def test_evaluator_override_gate_cannot_be_masked_by_passed_validation():
    from pathlib import Path

    expected = json.loads(Path("eval/development/client_01_clean.json").read_text())
    result = score_report(
        "", {"published_anyway": True, "validation": {"passed": True}}, expected
    )
    assert result["passed"] is False
    assert any(
        check["name"] == "no publication override" and not check["passed"]
        for check in result["checks"]
    )
