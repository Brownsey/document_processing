"""Real provider clients connected only to a test-owned HTTP handler."""

import httpx
from openai import OpenAI

from agent_pipeline.adapters import providers
from agent_pipeline.contracts import Ok

VALUE_SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}


def create_offline_provider(monkeypatch, handler, **settings):
    def factory(**options):
        assert options["max_retries"] == 0
        return OpenAI(
            **options,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

    monkeypatch.setattr(providers, "OpenAI", factory)
    monkeypatch.setattr(providers.time, "sleep", lambda _: None)
    result = providers.create_provider(**settings)
    assert isinstance(result, Ok)
    return result.value
