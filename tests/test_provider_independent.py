"""Independent provider boundary checks; every request uses an offline transport."""

import hashlib
import json
from dataclasses import asdict

import httpx
import pytest
from openai import OpenAI

from agent_pipeline import providers
from agent_pipeline.contracts import (
    ConfigError,
    Err,
    ExtractionError,
    Ok,
    ProviderError,
)

SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}


def payload(**changes):
    return {
        "id": "chat_independent",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-6-luna",
        "service_tier": "default",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": '{"value":"fact"}'},
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        **changes,
    }


@pytest.fixture
def offline(monkeypatch, tmp_path):
    requests = []
    replies = []

    def transport(request):
        requests.append(request)
        answer = replies.pop(0) if replies else payload()
        if callable(answer):
            answer = answer(request)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=answer)

    def factory(**settings):
        assert settings["max_retries"] == 0
        return OpenAI(
            **settings,
            http_client=httpx.Client(transport=httpx.MockTransport(transport)),
        )

    monkeypatch.setattr(providers, "OpenAI", factory)
    monkeypatch.setattr(providers.time, "sleep", lambda _: None)
    result = providers.create_provider(api_key="test-independent", cache_dir=tmp_path)
    assert isinstance(result, Ok)
    return result.value, requests, replies


def complete(provider, **changes):
    return provider.complete(
        **{
            "task": "extract",
            "instructions": "Extract only source-supported facts.",
            "context": {"client": "independent", "source_hash": "one"},
            "schema": SCHEMA,
            **changes,
        }
    )


def test_actual_image_task_reuses_validated_cache(offline):
    from agent_pipeline.evidence import IMAGE_SCHEMA

    provider, requests, replies = offline
    answer = payload()
    answer["choices"][0]["message"]["content"] = (
        '{"text":"visible text", "complete":true}'
    )
    replies.extend([answer, answer])
    first = complete(provider, task="read_image", schema=IMAGE_SCHEMA)
    assert isinstance(first, Ok)
    assert provider.approve_cache(first.value)
    assert isinstance(complete(provider, task="read_image", schema=IMAGE_SCHEMA), Ok)
    assert len(requests) == 1
    assert provider.records[-1]["cost_status"] == "not_billed"


def test_workflow_execution_wrapper_preserves_validated_image_cache(offline):
    from agent_pipeline.evidence import _read_image
    from agent_pipeline.workflow import BudgetPort

    provider, requests, replies = offline
    answer = payload()
    answer["choices"][0]["message"]["content"] = (
        '{"text":"visible text", "complete":true}'
    )
    replies.extend([answer, answer])
    raw = b"\x89PNG\r\n\x1a\nimage bytes"
    digest = hashlib.sha256(raw).hexdigest()
    port = BudgetPort(provider)
    for _ in range(2):
        result = _read_image(raw, "scan.png", digest, "independent", port)
        assert isinstance(result, Ok)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "data", [{"text": "partial", "complete": False}, {"text": " ", "complete": True}]
)
def test_unresolved_ocr_never_becomes_reusable_cache(offline, data):
    from agent_pipeline.evidence import IMAGE_SCHEMA

    provider, requests, replies = offline
    incomplete = payload()
    incomplete["choices"][0]["message"]["content"] = json.dumps(data)
    replies.extend([incomplete, incomplete])
    complete(provider, task="read_image", schema=IMAGE_SCHEMA)
    complete(provider, task="read_image", schema=IMAGE_SCHEMA)
    assert len(requests) == 2
    assert list(provider.cache_dir.glob("*.json")) == []


def test_dangling_local_schema_reference_fails_before_request(offline):
    provider, requests, _ = offline
    schema = {
        **SCHEMA,
        "properties": {"value": {"$ref": "#/$defs/nonexistent"}},
    }
    result = complete(provider, schema=schema)
    assert isinstance(result, Err)
    assert isinstance(result.error, ConfigError)
    assert requests == []


@pytest.mark.parametrize(
    "changes", [{"usage": "private client text"}, {"choices": [7]}]
)
def test_malformed_success_is_typed_safe_failure_and_metered(offline, changes, recwarn):
    provider, requests, replies = offline
    replies.append(payload(**changes))
    result = complete(provider)
    assert isinstance(result, Err)
    assert isinstance(result.error, ExtractionError)
    assert len(requests) == len(provider.records) == 1
    assert "private client text" not in json.dumps(asdict(result.error))
    assert "private client text" not in json.dumps(provider.records)
    assert "private client text" not in " ".join(str(item.message) for item in recwarn)
    assert list(provider.cache_dir.glob("*.json")) == []


def test_cached_failure_envelope_is_not_reused(offline):
    provider, requests, _ = offline
    first = complete(provider)
    assert isinstance(first, Ok)
    assert provider.approve_cache(first.value)
    entry = next(provider.cache_dir.glob("*.json"))
    document = json.loads(entry.read_text())
    document["outcome"] = "refused"
    entry.write_text(json.dumps(document))
    assert isinstance(complete(provider), Ok)
    assert len(requests) == 2


@pytest.mark.parametrize("corrupt", [None, 42, "private client text", []])
def test_nonobject_cache_is_a_miss(offline, corrupt):
    provider, requests, _ = offline
    first = complete(provider)
    assert isinstance(first, Ok)
    assert provider.approve_cache(first.value)
    entry = next(provider.cache_dir.glob("*.json"))
    entry.write_text(json.dumps(corrupt))
    assert isinstance(complete(provider), Ok)
    assert len(requests) == 2


def test_key_covers_source_schema_model_and_provider_code(offline, monkeypatch):
    provider, requests, _ = offline
    first = complete(provider)
    assert isinstance(first, Ok)
    assert provider.approve_cache(first.value)
    assert isinstance(complete(provider), Ok)
    assert len(requests) == 1
    assert isinstance(complete(provider, context={"client": "other"}), Ok)
    assert isinstance(complete(provider, context={"source_hash": "two"}), Ok)
    assert isinstance(complete(provider, instructions="Changed instructions"), Ok)
    assert isinstance(complete(provider, schema={**SCHEMA, "title": "Changed"}), Ok)
    provider.settings["model"] = "gpt-6-astra"
    assert isinstance(complete(provider), Ok)
    provider.settings["reasoning_effort"] = "high"
    assert isinstance(complete(provider), Ok)
    original_read = providers.Path.read_bytes

    def changed_code(path):
        content = original_read(path)
        return (
            content + b"\n# independent code change"
            if path.name == "providers.py"
            else content
        )

    monkeypatch.setattr(providers.Path, "read_bytes", changed_code)
    assert isinstance(complete(provider), Ok)
    assert len(requests) == 8


def test_unapproved_extraction_is_not_reused_or_persisted(offline):
    provider, requests, _ = offline
    assert isinstance(complete(provider), Ok)
    assert isinstance(complete(provider), Ok)
    assert len(requests) == 2
    assert list(provider.cache_dir.glob("*.json")) == []


def test_approval_is_specific_and_acceptance_marker_required(offline):
    provider, requests, _ = offline
    accepted = complete(provider)
    rejected = complete(provider, context={"client": "rejected"})
    assert isinstance(accepted, Ok)
    assert isinstance(rejected, Ok)
    assert provider.approve_cache(accepted.value)
    entries = list(provider.cache_dir.glob("*.json"))
    assert len(entries) == 1
    document = json.loads(entries[0].read_text())
    assert document["accepted"] is True
    assert isinstance(complete(provider, context={"client": "rejected"}), Ok)
    assert len(requests) == 3
    document["accepted"] = False
    entries[0].write_text(json.dumps(document))
    assert isinstance(complete(provider), Ok)
    assert len(requests) == 4


def test_connection_failure_is_bounded_safe_and_unknown_cost(offline):
    provider, requests, replies = offline
    replies.extend([httpx.ConnectError("private client text")] * 3)
    result = complete(provider)
    assert isinstance(result, Err)
    assert result.error.code == "connection"
    assert result.error.retryable is True
    assert len(requests) == len(provider.records) == 3
    assert all(record["cost_status"] == "unknown" for record in provider.records)
    assert "private client text" not in json.dumps(asdict(result.error))


@pytest.mark.parametrize(
    "settings", [{"provider": "unknown"}, {}, {"timeout": float("nan")}]
)
def test_unknown_provider_and_credentials_never_construct_client(monkeypatch, settings):
    def forbidden(**_):
        pytest.fail("Invalid configuration reached SDK construction")

    monkeypatch.setattr(providers, "OpenAI", forbidden)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = providers.create_provider(**settings)
    assert isinstance(result, Err)
    assert isinstance(result.error, ConfigError)


def test_cache_write_and_read_pricing_are_estimates():
    cost = providers._cost(
        {
            "input_tokens": 1000,
            "cached_input_tokens": 200,
            "cache_write_tokens": 100,
            "output_tokens": 50,
        },
        "openai",
        "gpt-6-luna",
        "default",
    )
    assert cost == pytest.approx(
        (700 * 0.10 + 200 * 0.01 + 100 * 0.125 + 50 * 0.50) / 1_000_000
    )
    assert providers._cost({}, "openai", "gpt-6-luna", "default") is None


def test_cli_explicit_openrouter_selection_has_no_fallback_or_network(monkeypatch):
    from agent_pipeline import generate

    selected = object()
    selections = []
    received = []

    def choose(**settings):
        selections.append(settings)
        return Ok(selected)

    def run(**arguments):
        received.append(arguments["provider"])
        return Err(ProviderError("authentication", "Credentials rejected.", "provider"))

    monkeypatch.setattr(generate, "load_dotenv", lambda: None)
    monkeypatch.setattr(generate, "create_provider", choose)
    monkeypatch.setattr(generate, "run_generation", run)
    assert (
        generate.main(
            [
                "--client",
                "independent",
                "--provider",
                "openrouter",
                "--model",
                "vendor/explicit-model",
                "--base-url",
                "https://openrouter.ai/api/v1",
            ]
        )
        == 1
    )
    assert len(selections) == 1
    assert selections[0]["provider"] == "openrouter"
    assert selections[0]["model"] == "vendor/explicit-model"
    assert selections[0]["base_url"] == "https://openrouter.ai/api/v1"
    assert received == [selected]


def test_workflow_deadline_is_shared_across_adapter_retries(offline, monkeypatch):
    from agent_pipeline.workflow import BudgetPort

    provider, requests, replies = offline
    now = [100.0]
    monkeypatch.setattr(providers.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        providers.time, "sleep", lambda delay: now.__setitem__(0, now[0] + delay)
    )

    def transient(_):
        now[0] += 0.5
        return httpx.ConnectError("private detail")

    replies.extend([transient, payload()])
    port = BudgetPort(provider, timeout=2)
    result = complete(port)
    assert isinstance(result, Ok)
    assert [request.extensions["timeout"]["read"] for request in requests] == [2, 0.5]
    assert len(provider.records) == 2
    now[0] = 103
    assert isinstance(complete(port), Err)
    assert len(requests) == 2


def test_permanent_error_preserved_when_response_crosses_deadline(offline, monkeypatch):
    from agent_pipeline.workflow import BudgetPort

    provider, requests, replies = offline
    now = [100.0]
    monkeypatch.setattr(providers.time, "monotonic", lambda: now[0])

    def late_authentication(_):
        now[0] = 103
        return httpx.Response(401, json={"error": {"message": "private detail"}})

    replies.append(late_authentication)
    result = complete(BudgetPort(provider, timeout=2))
    assert isinstance(result, Err)
    assert isinstance(result.error, ProviderError)
    assert result.error.code == "authentication"
    assert result.error.retryable is False
    assert len(requests) == len(provider.records) == 1
    assert provider.records[0]["outcome"] == "authentication"
