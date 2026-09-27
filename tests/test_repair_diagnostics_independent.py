"""Independent persistence and failure checks for repair diagnostics."""

import json
from datetime import datetime, timezone

import pytest
from support.workflow import CaseProvider, RepairProvider, configured_case

from agent_pipeline import workflow
from agent_pipeline.contracts import Err, ModelReply, Ok
from agent_pipeline.reporting import diagnostics


@pytest.mark.parametrize("kind", ["facts", "narrative", "shape", "reconcile"])
def test_exhausted_repairs_keep_each_trigger_and_existing_limits(tmp_path, kind):
    client, config, output, _ = configured_case(tmp_path)

    class RepeatedFailure(CaseProvider):
        def complete(self, **request):
            result = super().complete(**request)
            if kind in {"facts", "narrative"} and request["task"] == "validate_support":
                result.value.data.update(
                    supported=False,
                    issues=["Exact repeated source rejection"],
                    issue_kind=kind,
                )
            elif kind == "shape" and request["task"] == "write":
                return Ok(ModelReply("## Invalid heading", None, {}, "fake"))
            elif kind == "reconcile" and request["task"] == "extract":
                result.value.data["actions"][0]["refs"][0]["excerpt"] = "UNSUPPORTED"
            return result

    result = workflow.run_generation(
        client_dir=client,
        config_path=config,
        output_dir=output,
        provider=RepeatedFailure(),
    )
    assert isinstance(result, Err)
    manifest = json.loads((output / "client.manifest.json").read_text())
    category = (
        "facts" if kind == "reconcile" else "narrative" if kind == "shape" else kind
    )
    assert manifest["repair_counts"][category] == 2
    assert [item["attempt"] for item in manifest["repair_history"]] == [1, 2]
    files = list(output.glob("client_repair_*.md"))
    assert len(files) == 2
    assert len({event["diagnostic_path"] for event in manifest["repair_history"]}) == 2
    assert all(manifest["run_id"] in path.read_text(encoding="utf-8") for path in files)
    assert not (output / "client.md").exists()
    assert manifest["validation"]["passed"] is False


def test_optional_diagnostic_failure_is_visible_and_does_not_block_repaired_report(
    tmp_path, monkeypatch
):
    client, config, output, _ = configured_case(tmp_path)
    atomic = diagnostics.atomic_write

    def fail_diagnostic(path, text):
        if "_repair_" in path.name:
            raise PermissionError("Diagnostic directory denied")
        return atomic(path, text)

    monkeypatch.setattr(diagnostics, "atomic_write", fail_diagnostic)
    result = workflow.run_generation(
        client_dir=client,
        config_path=config,
        output_dir=output,
        provider=RepairProvider("facts", output),
    )
    assert isinstance(result, Ok)
    assert result.value["repair_counts"]["facts"] == 1
    assert result.value["repair_history"][0]["trigger"]["details"]["issues"]
    assert result.value["diagnostics"][0]["code"] == "repair_diagnostic_write_failed"
    assert not list(output.glob("*_repair_*.md"))
    assert (output / "client.md").exists()
    assert result.value["validation"]["passed"] is True


def test_same_clock_value_cannot_overwrite_previous_run_diagnostic(
    tmp_path, monkeypatch
):
    client, config, output, _ = configured_case(tmp_path)

    class FixedClock:
        @staticmethod
        def now(tz):
            return datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(diagnostics, "datetime", FixedClock)
    run_ids = []
    for _ in range(2):
        result = workflow.run_generation(
            client_dir=client,
            config_path=config,
            output_dir=output,
            provider=RepairProvider("facts", output),
        )
        assert isinstance(result, Ok)
        run_ids.append(result.value["run_id"])
    files = list(output.glob("client_repair_*.md"))
    assert len(files) == 2
    assert all(
        any(run_id in path.read_text(encoding="utf-8") for path in files)
        for run_id in run_ids
    )
