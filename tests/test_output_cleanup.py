import json
from pathlib import Path

from support.workflow import CaseProvider, configured_case

from agent_pipeline.contracts import Err
from agent_pipeline.reporting import diagnostics
from agent_pipeline.workflow import run_generation


def test_locked_previous_report_returns_error_and_writes_failed_manifest(
    tmp_path, monkeypatch
):
    client, config, output, _ = configured_case(tmp_path)
    output.mkdir()
    report = output / "client.md"
    report.write_text("Previous draft", encoding="utf-8")
    original = Path.unlink

    def unlink(path, *args, **kwargs):
        if path == report:
            raise PermissionError("Simulated locked report")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    provider = CaseProvider()
    result = run_generation(
        client_dir=client, config_path=config, output_dir=output, provider=provider
    )
    assert isinstance(result, Err)
    assert result.error.code == "output_write_failed"
    assert not provider.calls
    assert report.read_text(encoding="utf-8") == "Previous draft"
    manifest = json.loads((output / "client.manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["error"]["code"] == "output_write_failed"


def test_unwritable_manifest_removes_new_report_and_returns_error(
    tmp_path, monkeypatch
):

    client, config, output, _ = configured_case(tmp_path)
    original = diagnostics.atomic_write

    def write(path, text):
        if path.suffix == ".json" and (output / "client.md").exists():
            raise PermissionError("Simulated locked manifest")
        return original(path, text)

    monkeypatch.setattr(diagnostics, "atomic_write", write)
    result = run_generation(
        client_dir=client,
        config_path=config,
        output_dir=output,
        provider=CaseProvider(),
    )
    assert isinstance(result, Err)
    assert result.error.code == "output_write_failed"
    assert not (output / "client.md").exists()
