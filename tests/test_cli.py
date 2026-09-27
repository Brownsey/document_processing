import json

import httpx
import pytest
from openai import OpenAI

from agent_pipeline import generate
from agent_pipeline.adapters import providers
from agent_pipeline.evaluation import runner as evaluation


def test_evaluation_rejects_removed_mlflow_option_before_provider_setup(monkeypatch):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")

    def forbidden(**kwargs):
        pytest.fail("Removed option reached provider setup")

    monkeypatch.setattr(providers, "create_provider", forbidden)
    with pytest.raises(SystemExit) as error:
        evaluation.main(["--mlflow"])
    assert error.value.code == 2


def test_compare_keeps_both_configs_and_reports_failed_candidate(monkeypatch, tmp_path):
    from agent_pipeline.contracts import Ok

    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setattr(providers, "create_provider", lambda **kwargs: Ok(object()))
    configs = []

    def evaluate(**kwargs):
        configs.append(kwargs["config_path"].name)
        return {"passed": kwargs["config_path"].name == "baseline.json", "usage": []}

    monkeypatch.setattr(evaluation, "evaluate_case", evaluate)
    assert (
        evaluation.main(
            [
                "--mode",
                "compare",
                "--baseline-config",
                "baseline.json",
                "--config",
                "candidate.json",
                "--clients",
                "sample",
                "--output-dir",
                str(tmp_path),
            ]
        )
        == 1
    )
    summary = json.loads(next(tmp_path.glob("*/comparison.json")).read_text())
    assert configs == ["baseline.json", "candidate.json"]
    assert summary["variants"]["baseline"]["pass_rate"] == 1
    assert summary["variants"]["candidate"]["pass_rate"] == 0


@pytest.mark.parametrize("command", [generate, evaluation])
def test_cli_openrouter_options_reach_chat_request_and_diagnostics(
    command, monkeypatch, tmp_path
):
    """Catch missing CLI forwarding, wrong endpoint/key, and ignored tone/settings."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-test")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-use")
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(401, json={"error": {"message": "denied"}})

    monkeypatch.setattr(
        providers,
        "OpenAI",
        lambda **kwargs: OpenAI(
            **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(respond))
        ),
    )
    client = tmp_path / "data" / "sample"
    client.mkdir(parents=True)
    (client / "notes.txt").write_text("Sam discussed investing in an ISA.")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "document_title": "Report",
                "sections": [
                    {
                        "id": "intro",
                        "title": "Introduction",
                        "template": "<<text>>",
                        "placeholders": {"text": {"prompt": "Write a summary."}},
                    }
                ],
            }
        )
    )
    (tmp_path / "sample.json").write_text("{}")
    output = tmp_path / "outputs"
    arguments = [
        "--provider",
        "openrouter",
        "--model",
        "openai/gpt-6-luna",
        "--data-dir",
        str(client.parent),
        "--config",
        str(config),
        "--output-dir",
        str(output),
        "--timeout",
        "17",
        "--max-retries",
        "0",
        "--max-output-tokens",
        "3000",
        "--reasoning-effort",
        "low",
        "--run-timeout",
        "80",
        "--max-calls",
        "9",
        "--tone-of-voice",
        "Use concise British English.",
    ]
    arguments += (
        ["--client", "sample"]
        if command is generate
        else ["--clients", "sample", "--expectations-dir", str(tmp_path)]
    )
    assert command.main(arguments) == 1
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer router-test"
    body = json.loads(request.content)
    assert body["model"] == "openai/gpt-6-luna"
    assert body["max_tokens"] == 3000
    assert body["reasoning"] == {"effort": "low"}
    assert "Use concise British English." in body["messages"][0]["content"]
    manifest = json.loads(next(output.rglob("sample.manifest.json")).read_text())
    assert manifest["error"]["code"] == "authentication"
    assert manifest["settings"]["timeout"] == 17
    assert manifest["settings"]["max_retries"] == 0
    assert manifest["execution_limits"] == {"timeout": 80, "max_calls": 9}
    assert not list(output.rglob("sample.md"))
    if command is evaluation:
        snapshot = json.loads(next(output.rglob("snapshot/config.json")).read_text())
        assert snapshot["tone_of_voice"] == "Use concise British English."


@pytest.mark.parametrize("command", [generate, evaluation])
@pytest.mark.parametrize(
    "option",
    [
        ["--cap-usd", "10"],
        ["--ledger", "custom.sqlite3"],
        ["--max-calls", "0"],
        ["--run-timeout", "nan"],
        ["--run-timeout", "0"],
        ["--tone-of-voice", " "],
    ],
)
def test_cli_rejects_unsupported_or_invalid_runtime_options(command, option, tmp_path):
    arguments = ["--provider", "openrouter", "--model", "openai/gpt-6-luna"]
    arguments += ["--client" if command is generate else "--clients", "sample"]
    arguments += ["--output-dir", str(tmp_path)]
    with pytest.raises(SystemExit) as error:
        command.main(arguments + option)
    assert error.value.code == 2
