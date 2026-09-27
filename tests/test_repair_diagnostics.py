import json
import re

import pytest
from test_workflow import CaseProvider, configured_case

from agent_pipeline.contracts import Err, ModelReply, Ok, ProviderError
from agent_pipeline.workflow import run_generation


class RepairProvider(CaseProvider):
    def __init__(self, trigger, output, *, fail_after=False):
        super().__init__()
        self.trigger = trigger
        self.output = output
        self.fail_after = fail_after
        self.triggered = False

    def complete(self, **request):
        if self.triggered and self.fail_after:
            # Evidence must be on disk before the retry, not just at run completion.
            assert list(self.output.glob("client_repair_*.md"))
            return Err(ProviderError("unavailable", "Provider unavailable", "provider"))
        result = super().complete(**request)
        if self.triggered:
            return result
        if self.trigger == "reconcile" and request["task"] == "extract":
            result.value.data["actions"][0]["refs"][0]["excerpt"] = (
                "UNSUPPORTED-EXCERPT"
            )
        elif self.trigger == "shape" and request["task"] == "write":
            result = Ok(ModelReply("## Unexpected heading", None, {}, "fake"))
        elif (
            self.trigger in {"facts", "narrative"}
            and request["task"] == "validate_support"
        ):
            result.value.data.update(
                supported=False,
                issues=['The draft says "retire now"; evidence says "retire later".'],
                issue_kind=self.trigger,
            )
        else:
            return result
        self.triggered = True
        return result


@pytest.mark.parametrize("trigger", ["facts", "narrative", "shape", "reconcile"])
@pytest.mark.parametrize("fail_after", [False, True])
def test_repair_reason_is_saved_before_retry_and_survives_run_outcome(
    tmp_path, trigger, fail_after
):
    client, config, output, _ = configured_case(tmp_path)
    result = run_generation(
        client_dir=client,
        config_path=config,
        output_dir=output,
        provider=RepairProvider(trigger, output, fail_after=fail_after),
    )
    if fail_after:
        assert isinstance(result, Err)
        assert result.error.code == "unavailable"
    else:
        assert isinstance(result, Ok)
    files = list(output.glob("client_repair_*.md"))
    assert len(files) == 1
    assert re.fullmatch(r"client_repair_\d{8}T\d{12}Z\.md", files[0].name)
    text = files[0].read_text(encoding="utf-8")
    manifest = json.loads((output / "client.manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] in text
    assert manifest["repair_history"][0]["diagnostic_path"] == str(files[0].resolve())
    if trigger in {"facts", "narrative"}:
        assert "retire now" in text and "retire later" in text
        assert "Your objective is long-term flexibility." in text
    elif trigger == "shape":
        assert "unexpected_structure" in text and "## Unexpected heading" in text
        assert "body" in text
    else:
        assert "UNSUPPORTED-EXCERPT" in text
        assert "reconcile" in text


def test_clean_run_creates_no_repair_diagnostic(tmp_path):
    client, config, output, _ = configured_case(tmp_path)
    result = run_generation(
        client_dir=client,
        config_path=config,
        output_dir=output,
        provider=CaseProvider(),
    )
    assert isinstance(result, Ok)
    assert not list(output.glob("*_repair_*.md"))


def test_repair_diagnostics_survive_later_runs(tmp_path):
    client, config, output, _ = configured_case(tmp_path)
    for _ in range(2):
        result = run_generation(
            client_dir=client,
            config_path=config,
            output_dir=output,
            provider=RepairProvider("facts", output),
        )
        assert isinstance(result, Ok)
    assert len(list(output.glob("client_repair_*.md"))) == 2
