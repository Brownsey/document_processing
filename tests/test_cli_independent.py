"""Independent CLI boundary checks using only offline transports."""

import json

import httpx
import pytest
from openai import OpenAI

from agent_pipeline import evaluation, generate, providers
from agent_pipeline.contracts import Err, Ok


@pytest.fixture(autouse=True)
def isolated_credentials(monkeypatch):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "independent-router-key")


def test_evaluation_defaults_to_all_four_with_selected_provider(monkeypatch, tmp_path):
    monkeypatch.setattr(providers, "OpenAI", lambda **kwargs: object())
    cases = []

    def evaluate(**kwargs):
        cases.append(kwargs)
        return {"passed": True, "score": 1, "usage": []}

    monkeypatch.setattr(evaluation, "evaluate_case", evaluate)
    assert (
        evaluation.main(
            [
                "--provider",
                "openrouter",
                "--model",
                "vendor/model",
                "--output-dir",
                str(tmp_path),
                "--run-timeout",
                "47",
                "--max-calls",
                "3",
                "--tone-of-voice",
                "Short sentences.",
            ]
        )
        == 0
    )
    assert [case["client_dir"].name for case in cases] == [
        "client_01_clean",
        "client_02_medium",
        "client_03_hard",
        "client_04_stretch",
    ]
    for case in cases:
        assert case["provider"].settings["provider"] == "openrouter"
        assert case["provider"].settings["model"] == "vendor/model"
        assert case["run_timeout"] == 47
        assert case["max_calls"] == 3
        assert case["tone_of_voice"] == "Short sentences."


@pytest.mark.parametrize("command", [generate, evaluation])
@pytest.mark.parametrize(
    "option",
    [
        ["--timeout", "nan"],
        ["--timeout", "-1"],
        ["--timeout", "601"],
        ["--max-retries", "-1"],
        ["--max-output-tokens", "0"],
        ["--base-url", "https://example.com/v1"],
    ],
)
def test_invalid_provider_options_never_construct_client(
    command, option, monkeypatch, tmp_path
):
    def forbidden(**kwargs):
        pytest.fail("Invalid arguments reached SDK construction")

    monkeypatch.setattr(providers, "OpenAI", forbidden)
    monkeypatch.setattr(
        generate,
        "run_generation",
        lambda **kwargs: kwargs["provider"].complete(
            task="write", instructions="Write", context={}
        ),
    )
    arguments = [
        "--provider",
        "openrouter",
        "--model",
        "vendor/model",
        "--output-dir",
        str(tmp_path),
        "--client" if command is generate else "--clients",
        "sample",
    ]
    assert command.main(arguments + option) == 1


@pytest.mark.parametrize("command", [generate, evaluation])
def test_openrouter_requires_model_before_client_creation(command, monkeypatch):
    monkeypatch.setattr(
        providers, "OpenAI", lambda **kwargs: pytest.fail("SDK created")
    )
    args = ["--provider", "openrouter"]
    if command is generate:
        args += ["--client", "sample"]
    with pytest.raises(SystemExit) as error:
        command.main(args)
    assert error.value.code == 2


def test_cli_retry_timeout_and_default_output_are_effective(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(429, json={"error": {"message": "retry"}})

    monkeypatch.setattr(
        providers,
        "OpenAI",
        lambda **kwargs: OpenAI(
            **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(respond))
        ),
    )
    monkeypatch.setattr(providers.time, "sleep", lambda _: None)

    def run(**kwargs):
        assert str(kwargs["output_dir"]) == "outputs"
        result = kwargs["provider"].complete(
            task="write", instructions="Write", context={}
        )
        assert isinstance(result, Err)
        assert result.error.code == "rate_limit"
        return result

    monkeypatch.setattr(generate, "run_generation", run)
    assert (
        generate.main(
            [
                "--client",
                "sample",
                "--provider",
                "openrouter",
                "--model",
                "vendor/model",
                "--timeout",
                "7",
                "--max-retries",
                "1",
                "--reasoning-effort",
                "none",
            ]
        )
        == 1
    )
    assert len(requests) == 2
    for request in requests:
        assert request.headers["authorization"] == "Bearer independent-router-key"
        assert request.extensions["timeout"]["read"] == 7
        assert json.loads(request.content)["reasoning"] == {"effort": "none"}


def test_evaluation_validates_every_client_before_any_generation(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(providers, "create_provider", lambda **kwargs: Ok(object()))

    def evaluate(**kwargs):
        calls.append(kwargs)
        return {"passed": True, "score": 1, "usage": []}

    monkeypatch.setattr(evaluation, "evaluate_case", evaluate)
    with pytest.raises(SystemExit) as error:
        evaluation.main(
            [
                "--provider",
                "openrouter",
                "--model",
                "vendor/model",
                "--clients",
                "valid",
                "../invalid",
                "--output-dir",
                str(tmp_path),
            ]
        )
    assert error.value.code == 2
    assert calls == []


def test_router_missing_key_never_falls_back_to_openai(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-provider-key")
    monkeypatch.setattr(
        providers, "OpenAI", lambda **kwargs: pytest.fail("SDK created")
    )
    assert (
        evaluation.main(
            [
                "--provider",
                "openrouter",
                "--model",
                "vendor/model",
                "--output-dir",
                str(tmp_path),
            ]
        )
        == 1
    )
    summary = json.loads(next(tmp_path.rglob("comparison.json")).read_text())
    assert summary["stopping_reason"] == "missing_credentials"
    assert len(summary["runs"]) == 1


def test_generation_cli_call_limit_stops_real_workflow(monkeypatch, tmp_path):
    from test_workflow import CaseProvider, configured_case

    client, config, output, _ = configured_case(tmp_path)
    provider = CaseProvider()
    monkeypatch.setattr(generate, "create_provider", lambda **kwargs: Ok(provider))
    assert (
        generate.main(
            [
                "--client",
                client.name,
                "--data-dir",
                str(client.parent),
                "--config",
                str(config),
                "--output-dir",
                str(output),
                "--max-calls",
                "1",
            ]
        )
        == 1
    )
    manifest = json.loads((output / "client.manifest.json").read_text())
    assert manifest["error"]["code"] == "execution_limit"
    assert len(provider.calls) == 1
    assert not (output / "client.md").exists()
