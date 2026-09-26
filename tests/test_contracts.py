import importlib.util
from dataclasses import FrozenInstanceError

import pytest


def test_results_preserve_typed_errors_and_are_frozen():
    assert importlib.util.find_spec("agent_pipeline.contracts") is not None
    from agent_pipeline.contracts import Err, Ok, ProviderError

    error = ProviderError(
        "timeout", "Request timed out", stage="extract", retryable=True
    )
    result = Err(error)
    assert result.error is error
    assert result.error.retryable
    assert Ok(42).value == 42
    with pytest.raises(FrozenInstanceError):
        setattr(result, "error", ProviderError("other", "Other"))


def test_mutable_metadata_is_not_shared_between_contract_instances():
    assert importlib.util.find_spec("agent_pipeline.contracts") is not None
    from agent_pipeline.contracts import EvidenceBundle, PipelineError

    first, second = PipelineError("a", "a"), PipelineError("b", "b")
    first.details["key"] = 1
    assert second.details == {}
    assert EvidenceBundle([], [], []).databases == []


def test_model_reply_cache_key_is_optional_and_positional_compatible():
    from agent_pipeline.contracts import ModelReply

    reply = ModelReply("answer", None, {}, "model")
    assert getattr(reply, "cache_key", "missing") is None
    assert (
        ModelReply("answer", {}, {}, "model", cache_key="validated-key").cache_key
        == "validated-key"
    )
