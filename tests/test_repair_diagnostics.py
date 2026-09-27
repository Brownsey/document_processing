import json
import re

import pytest
from support.workflow import CaseProvider, RepairProvider, configured_case

from agent_pipeline.contracts import Err, Ok
from agent_pipeline.workflow import run_generation


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
