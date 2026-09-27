"""Shared tone reaches the model boundary without changing evidence or contracts."""

import json

import pytest
from test_workflow import CaseProvider, configured_case

from agent_pipeline.contracts import Err, ModelReply, Ok
from agent_pipeline.workflow import BudgetPort, run_generation


@pytest.mark.parametrize(
    "task",
    ["read_image", "extract", "investigate", "inclusion", "write", "validate_support"],
)
def test_shared_tone_reaches_every_model_task_without_rewriting_payload(task):
    calls = []

    class Provider:
        def complete(self, **request):
            calls.append(request)
            return Ok(ModelReply("Exact source quotation", None, {}, "fake"))

    context = {"evidence": "KEEP exactly: a-b, GBP 7,251", "account_id": "e1"}
    schema = {"type": "object", "properties": {"quoted_text": {"type": "string"}}}
    port = BudgetPort(Provider(), tone_of_voice="Calm, direct and concise.")
    reply = port.complete(
        task=task, instructions="Task contract", context=context, schema=schema
    )
    request = calls[0]
    assert "Calm, direct and concise." in request["instructions"]
    assert "Task contract" in request["instructions"]
    assert "verbatim" in request["instructions"]
    assert request["context"] == context and request["schema"] == schema
    assert isinstance(reply, Ok) and reply.value.text == "Exact source quotation"


def test_tone_config_change_reaches_run_and_changes_prompt_fingerprint(tmp_path):
    client, config, output, template = configured_case(tmp_path)
    fingerprints = []
    for tone in ["Calm and concise.", "Formal and measured."]:
        template["tone_of_voice"] = tone
        config.write_text(json.dumps(template), encoding="utf-8")
        provider = CaseProvider()
        result = run_generation(
            client_dir=client, config_path=config, output_dir=output, provider=provider
        )
        assert isinstance(result, Ok), result
        assert all(tone in c["instructions"] for c in provider.calls)
        fingerprints.append(result.value["fingerprints"]["prompts"])
    assert fingerprints[0] != fingerprints[1]


@pytest.mark.parametrize("tone", [None, 42, [], "", "   "])
def test_invalid_tone_fails_before_model_calls(tmp_path, tone):
    client, config, output, template = configured_case(tmp_path)
    template["tone_of_voice"] = tone
    config.write_text(json.dumps(template), encoding="utf-8")
    provider = CaseProvider()
    result = run_generation(
        client_dir=client, config_path=config, output_dir=output, provider=provider
    )
    assert isinstance(result, Err) and result.error.code == "invalid_config"
    assert provider.calls == []


def test_legacy_config_keeps_original_instructions(tmp_path):
    client, config, output, _ = configured_case(tmp_path)
    provider = CaseProvider()
    result = run_generation(
        client_dir=client, config_path=config, output_dir=output, provider=provider
    )
    assert isinstance(result, Ok)
    write = next(c for c in provider.calls if c["task"] == "write")
    assert write["instructions"] == "\n\nWrite objective"
