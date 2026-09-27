"""Adapter boundary tests use real SDK serialization with an offline transport."""

import json

import httpx
import pytest
from openai import OpenAI

from agent_pipeline import providers
from agent_pipeline.contracts import (
    ConfigError,
    Err,
    ExecutionLimitExceeded,
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


def test_decimal_schema_uses_supported_wire_pattern_and_keeps_local_validation():
    from agent_pipeline.domain import CaseFacts

    original = CaseFacts.model_json_schema()
    wire = providers._strict_schema(original)
    assert "(?!" not in json.dumps(wire)
    assert "(?!" in json.dumps(original)
    decimal_schema = wire["$defs"]["Fee"]["properties"]["rate_percent"]["anyOf"][1]
    import jsonschema

    jsonschema.validate("12.50", decimal_schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate("not-a-number", decimal_schema)


def response(
    text='{"value":"supported"}', *, finish_reason="stop", refusal=None, **overrides
):
    return {
        "id": "chat_offline",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-6-luna",
        "service_tier": "default",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": text, "refusal": refusal},
            }
        ],
        "usage": {
            "prompt_tokens": 1000,
            "completion_tokens": 100,
            "prompt_tokens_details": {"cached_tokens": 100},
            "completion_tokens_details": {"reasoning_tokens": 20},
            "total_tokens": 1100,
        },
        **overrides,
    }


def adapter(monkeypatch, replies, **settings):
    requests = []

    def handler(request):
        requests.append(
            {
                **json.loads(request.content),
                "_http_timeout": request.extensions.get("timeout"),
                "_path": request.url.path,
            }
        )
        reply = replies.pop(0)
        if callable(reply):
            reply = reply(request)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, tuple):
            return httpx.Response(reply[0], json=reply[1])
        return httpx.Response(200, json=reply)

    original = OpenAI

    def factory(**kwargs):
        assert kwargs["max_retries"] == 0
        return original(
            **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler))
        )

    monkeypatch.setattr(providers, "OpenAI", factory)
    monkeypatch.setattr(providers.time, "sleep", lambda _: None)
    result = providers.create_provider(api_key="test-key", **settings)
    assert isinstance(result, Ok)
    assert requests == []
    return result.value, requests


def complete(provider, **changes):
    return provider.complete(
        **{
            "task": "extract",
            "instructions": "Extract facts",
            "context": {"client": "A"},
            "schema": SCHEMA,
            **changes,
        }
    )


def accepted(provider, **changes):
    result = complete(provider, **changes)
    assert isinstance(result, Ok)
    assert provider.approve_cache(result.value)
    return result


def test_structured_success_records_estimated_usage_and_separates_instructions(
    monkeypatch,
):
    provider, requests = adapter(monkeypatch, [response()])
    result = complete(provider)
    assert isinstance(result, Ok)
    assert result.value.data == {"value": "supported"}
    assert requests[0]["_path"] == "/v1/chat/completions"
    assert requests[0]["messages"][0]["content"] == "Extract facts"
    assert requests[0]["max_completion_tokens"] == 20000
    assert requests[0]["reasoning_effort"] == "low"
    assert requests[0]["response_format"]["json_schema"]["schema"] == SCHEMA
    assert requests[0]["store"] is False
    record = provider.records[0]
    assert record["estimated_cost_usd"] == pytest.approx(0.000141)
    assert record["cost_status"] == "estimated"
    assert record["task"] == "extract"
    assert "test-key" not in json.dumps(provider.settings)


def test_spend_cap_blocks_before_network_and_persists_charges(monkeypatch, tmp_path):
    from decimal import Decimal

    from agent_pipeline.budget import MoneyBudget

    ledger = tmp_path / "spend.sqlite3"
    provider, requests = adapter(
        monkeypatch, [response()], cap_usd=Decimal("1"), ledger_path=ledger
    )
    assert isinstance(complete(provider), Ok)
    spent, reserved = MoneyBudget(ledger, Decimal("1")).totals()
    assert spent > 0 and reserved == 0
    blocked, no_requests = adapter(
        monkeypatch,
        [],
        cap_usd=Decimal("0.000001"),
        ledger_path=tmp_path / "blocked.sqlite3",
    )
    result = complete(blocked)
    assert isinstance(result, Err) and result.error.code == "spend_limit"
    assert no_requests == []


@pytest.mark.parametrize(
    "settings",
    [
        {"provider": "unknown"},
        {"model": ""},
        {"max_retries": -1},
        {"timeout": 0},
        {"max_output_tokens": 0},
        {"tier": "flex"},
        {"provider": "openrouter", "model": "vendor/model"},
        {"provider": "openai", "base_url": "https://evil.invalid"},
    ],
)
def test_invalid_setup_is_local_typed_failure(settings):
    result = providers.create_provider(api_key="test", **settings)
    assert isinstance(result, Err)
    assert isinstance(result.error, ConfigError)


def test_missing_credentials_is_local_failure(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert isinstance(providers.create_provider(), Err)


@pytest.mark.parametrize(
    "status,code,retryable,calls",
    [
        (400, "invalid_request", False, 1),
        (401, "authentication", False, 1),
        (403, "permission", False, 1),
        (404, "model_unavailable", False, 1),
        (429, "rate_limit", True, 3),
        (500, "service_unavailable", True, 3),
    ],
)
def test_errors_are_safe_metered_and_retries_bounded(
    monkeypatch, status, code, retryable, calls
):
    provider, requests = adapter(
        monkeypatch, [(status, {"error": {"message": "SECRET CLIENT RAW"}})] * 3
    )
    result = complete(provider)
    assert isinstance(result, Err)
    assert isinstance(result.error, ProviderError)
    assert result.error.code == code
    assert result.error.retryable is retryable
    assert len(requests) == calls == len(provider.records)
    assert all(r["estimated_cost_usd"] is None for r in provider.records)
    assert "SECRET" not in str(result.error)
    assert "SECRET" not in json.dumps(provider.records)


def test_transient_timeout_then_success_records_both_attempts(monkeypatch):
    provider, requests = adapter(
        monkeypatch, [httpx.ReadTimeout("private"), response()]
    )
    assert isinstance(complete(provider), Ok)
    assert len(requests) == 2
    assert provider.records[0]["cost_status"] == "unknown"
    assert provider.records[1]["attempt"] == 2


@pytest.mark.parametrize(
    "reply",
    [
        response("not json"),
        response('{"value":42}'),
        response(""),
        response(finish_reason="length"),
        response(refusal="private refusal"),
    ],
)
def test_invalid_refused_incomplete_output_never_cached(monkeypatch, tmp_path, reply):
    provider, requests = adapter(monkeypatch, [reply, response()], cache_dir=tmp_path)
    result = complete(provider)
    assert isinstance(result, Err)
    assert isinstance(result.error, ExtractionError)
    assert isinstance(complete(provider), Ok)
    assert len(requests) == 2
    assert provider.records[0]["estimated_cost_usd"] is not None


def test_cache_key_changes_with_context_prompt_schema_and_image(monkeypatch, tmp_path):
    provider, requests = adapter(
        monkeypatch, [response()] * 5, cache_dir=tmp_path / "cache"
    )
    assert isinstance(accepted(provider), Ok)
    assert isinstance(complete(provider), Ok)
    assert len(requests) == 1
    assert provider.records[-1]["outcome"] == "cache_hit"
    assert provider.records[-1]["estimated_cost_usd"] == 0
    assert isinstance(complete(provider, context={"client": "B"}), Ok)
    assert isinstance(complete(provider, instructions="Different"), Ok)
    image = tmp_path / "scan.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfirst")
    assert isinstance(complete(provider, image_path=image), Ok)
    image.write_bytes(b"\x89PNG\r\n\x1a\nchanged")
    assert isinstance(complete(provider, image_path=image), Ok)
    assert len(requests) == 5
    assert requests[-1]["messages"][1]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


def test_narratives_are_fresh_even_with_cache(monkeypatch, tmp_path):
    provider, requests = adapter(
        monkeypatch, [response(), response()], cache_dir=tmp_path
    )
    complete(provider, task="narrative")
    complete(provider, task="narrative")
    assert len(requests) == 2


def test_bad_schema_and_missing_image_make_no_requests(monkeypatch, tmp_path):
    provider, requests = adapter(monkeypatch, [])
    assert isinstance(complete(provider, schema={"type": "invalid"}), Err)
    assert isinstance(complete(provider, image_path=tmp_path / "missing.png"), Err)
    assert requests == []


def test_openrouter_explicit_chat_wire_uses_unknown_pricing(monkeypatch):
    reply = {
        "id": "chat",
        "object": "chat.completion",
        "created": 1,
        "model": "vendor/model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": '{"value":"supported"}'},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    provider, requests = adapter(
        monkeypatch,
        [reply],
        provider="openrouter",
        model="vendor/model",
        base_url="https://openrouter.ai/api/v1",
    )
    assert isinstance(complete(provider), Ok)
    assert requests[0]["response_format"]["type"] == "json_schema"
    assert provider.records[0]["estimated_cost_usd"] is None


def test_astra_rewrite_is_explicit_and_accounted(monkeypatch):
    provider, requests = adapter(
        monkeypatch, [response(model="gpt-6-astra")], model="gpt-6-astra"
    )
    assert isinstance(complete(provider, task="prompt_rewrite"), Ok)
    assert requests[0]["model"] == "gpt-6-astra"
    assert provider.records[0]["estimated_cost_usd"] == pytest.approx(0.0141)


def test_external_schema_references_and_non_json_context_never_call(monkeypatch):
    provider, requests = adapter(monkeypatch, [])
    external = {
        "type": "object",
        "properties": {"value": {"$ref": "https://example.org/schema"}},
    }
    assert isinstance(complete(provider, schema=external), Err)
    assert isinstance(complete(provider, context={"bad": float("nan")}), Err)
    assert requests == []


def test_cache_schema_and_model_settings_change_invalidate(monkeypatch, tmp_path):
    provider, requests = adapter(
        monkeypatch, [response(), response()], cache_dir=tmp_path
    )
    accepted(provider)
    complete(provider, schema={**SCHEMA, "description": "Version 2"})
    assert len(requests) == 2
    other, other_requests = adapter(
        monkeypatch, [response()], cache_dir=tmp_path, reasoning_effort="high"
    )
    complete(other)
    assert len(other_requests) == 1
    assert provider.fingerprint != other.fingerprint


def test_corrupt_cache_is_not_returned_as_valid_facts(monkeypatch, tmp_path):
    provider, requests = adapter(
        monkeypatch, [response(), response()], cache_dir=tmp_path
    )
    accepted(provider)
    next(tmp_path.glob("*.json")).write_text('{"data":{"value":42}}', encoding="utf-8")
    assert isinstance(complete(provider), Ok)
    assert len(requests) == 2


def test_missing_image_returns_input_error(monkeypatch, tmp_path):
    from agent_pipeline.contracts import InputError

    provider, _ = adapter(monkeypatch, [])
    result = complete(provider, image_path=tmp_path / "missing.png")
    assert isinstance(result, Err)
    assert isinstance(result.error, InputError)


def test_unknown_model_and_missing_usage_are_unknown_cost(monkeypatch):
    provider, _ = adapter(
        monkeypatch, [response(model="unpriced-model"), response(usage=None)]
    )
    complete(provider)
    complete(provider)
    assert all(record["estimated_cost_usd"] is None for record in provider.records)
    assert provider.records[0]["usage"]["input_tokens"] == 1000


def test_reasoning_usage_does_not_obscure_structured_message(monkeypatch):
    provider, _ = adapter(monkeypatch, [response()])
    result = complete(provider)
    assert isinstance(result, Ok)
    assert result.value.data == {"value": "supported"}
    assert result.value.usage["reasoning_tokens"] == 20


def test_malformed_endpoint_is_config_error():
    result = providers.create_provider(
        "openrouter", "vendor/model", api_key="test", base_url="https://[broken"
    )
    assert isinstance(result, Err)
    assert isinstance(result.error, ConfigError)


def test_unapproved_extraction_is_fresh_and_approval_rejects_changed_reply(
    monkeypatch, tmp_path
):
    from dataclasses import replace

    provider, requests = adapter(
        monkeypatch, [response(), response()], cache_dir=tmp_path
    )
    first = complete(provider)
    assert isinstance(first, Ok)
    assert list(tmp_path.glob("*.json")) == []
    assert not provider.approve_cache(replace(first.value, data={"value": "invented"}))
    second = complete(provider)
    assert isinstance(second, Ok)
    assert len(requests) == 2
    assert provider.approve_cache(second.value)
    assert isinstance(complete(provider), Ok)
    assert len(requests) == 2


def test_cache_non_object_json_is_a_miss(monkeypatch, tmp_path):
    provider, requests = adapter(
        monkeypatch, [response(), response()], cache_dir=tmp_path
    )
    accepted(provider)
    next(tmp_path.glob("*.json")).write_text('"invalid"', encoding="utf-8")
    assert isinstance(complete(provider), Ok)
    assert len(requests) == 2


def test_deadline_bounds_each_attempt_and_resets_for_next_run(monkeypatch):
    now = [100.0]

    def first_attempt(_):
        now[0] += 3
        return 500, {"error": {"message": "temporary"}}

    provider, requests = adapter(monkeypatch, [first_attempt, response(), response()])
    monkeypatch.setattr(providers.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        providers.time, "sleep", lambda delay: now.__setitem__(0, now[0] + delay)
    )
    provider.set_deadline(110.0)
    assert isinstance(complete(provider), Ok)
    assert requests[0]["_http_timeout"]["read"] == 10
    assert requests[1]["_http_timeout"]["read"] == 6
    now[0] = 111.0
    assert isinstance(complete(provider), Err)
    assert len(requests) == 2
    provider.set_deadline(120.0)
    assert isinstance(complete(provider), Ok)
    assert requests[2]["_http_timeout"]["read"] == 9


def test_retry_delay_cannot_exhaust_run_deadline(monkeypatch):
    now = [100.0]
    provider, requests = adapter(
        monkeypatch, [(500, {"error": {"message": "temporary"}})]
    )
    monkeypatch.setattr(providers.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        providers.time,
        "sleep",
        lambda _: pytest.fail("Must abort before an unaffordable retry delay"),
    )
    provider.set_deadline(100.5)
    result = complete(provider)
    assert isinstance(result, Err)
    assert isinstance(result.error, ExecutionLimitExceeded)
    assert len(requests) == len(provider.records) == 1
    assert provider.records[0]["cost_status"] == "unknown"


def test_late_success_is_metered_but_not_returned_or_cached(monkeypatch, tmp_path):
    now = [100.0]

    def late(_):
        now[0] = 111.0
        return response()

    provider, requests = adapter(monkeypatch, [late], cache_dir=tmp_path)
    monkeypatch.setattr(providers.time, "monotonic", lambda: now[0])
    provider.set_deadline(110.0)
    result = complete(provider)
    assert isinstance(result, Err)
    assert isinstance(result.error, ExecutionLimitExceeded)
    assert len(requests) == len(provider.records) == 1
    assert provider.records[0]["estimated_cost_usd"] == pytest.approx(0.000141)
    assert not list(tmp_path.glob("*.json"))
