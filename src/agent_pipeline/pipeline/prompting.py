"""Structured model requests, shared tone and execution limits."""

import json
import time

from pydantic import BaseModel, ValidationError

from agent_pipeline.contracts import (
    Err,
    ExecutionLimitExceeded,
    ExtractionError,
    ModelPort,
    Ok,
)


class ModelSession:
    """Apply run-wide tone and execution bounds; the adapter owns transport retries."""

    def __init__(
        self,
        provider: ModelPort,
        *,
        timeout: float = 300,
        max_calls: int = 40,
        tone_of_voice: str = "",
    ):
        self.provider = provider
        self.tone_of_voice = tone_of_voice
        self.started = time.monotonic()
        self.timeout = timeout
        self.max_calls = max_calls
        self.calls = 0
        self.latest_extraction_reply = None
        set_deadline = getattr(provider, "set_deadline", None)
        if set_deadline:
            set_deadline(self.started + timeout)

    def approve_cache(self, reply) -> bool:
        approve = getattr(self.provider, "approve_cache", None)
        return bool(approve(reply)) if approve else False

    def complete(self, **kwargs):
        if (
            time.monotonic() - self.started >= self.timeout
            or self.calls >= self.max_calls
        ):
            return Err(
                ExecutionLimitExceeded(
                    "execution_limit",
                    "Generation reached its execution limit.",
                    kwargs.get("task", "generation"),
                )
            )
        self.calls += 1
        if self.tone_of_voice:
            kwargs["instructions"] = (
                "Shared tone of voice (generated prose only):\n"
                + self.tone_of_voice
                + "\nTask contracts take precedence over style. Preserve verbatim transcription, "
                "quotations, names, identifiers, figures, qualifiers and required JSON/schema values. "
                "Do not change facts, decisions, conditions or output structure to suit the tone.\n\n"
                + kwargs["instructions"]
            )
        result = self.provider.complete(**kwargs)
        if isinstance(result, Err):
            return result
        if time.monotonic() - self.started >= self.timeout:
            return Err(
                ExecutionLimitExceeded(
                    "run_timeout",
                    "Generation exceeded its run timeout.",
                    kwargs.get("task", "generation"),
                )
            )
        return result


def request_structured(
    port, *, schema: type[BaseModel], task: str, instructions: str, context: dict
):
    definition = schema.model_json_schema()

    def strict(node):
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for child in node.values():
                strict(child)
        elif isinstance(node, list):
            for child in node:
                strict(child)

    strict(definition)
    # Short, run-local labels avoid asking the model to copy 64-character hashes.
    # Restore canonical IDs before reconciliation or publication.
    aliases = (
        {
            block["id"]: f"e{index + 1}"
            for index, block in enumerate(context.get("evidence", []))
        }
        if task == "extract"
        else {}
    )

    def relabel(value, mapping):
        if isinstance(value, dict):
            return {
                key: mapping.get(item, item)
                if key == "evidence_id" and isinstance(item, str)
                else relabel(item, mapping)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [relabel(item, mapping) for item in value]
        return value

    prompt_context = relabel(context, aliases)
    for block in prompt_context.get("evidence", []):
        block["id"] = aliases.get(block["id"], block["id"])
    for retrieval in prompt_context.get("focused_evidence", []):
        if retrieval.get("tool") in {"read", "search"}:
            for block in retrieval.get("result", []):
                block["id"] = aliases.get(block["id"], block["id"])

    reply = port.complete(
        task=task,
        instructions=instructions,
        context=prompt_context,
        schema=definition,
    )
    if isinstance(reply, Err):
        return reply
    try:
        data = (
            reply.value.data
            if reply.value.data is not None
            else json.loads(reply.value.text)
        )
        validated = schema.model_validate(
            relabel(data, {v: k for k, v in aliases.items()})
        )
        if task == "extract":
            port.latest_extraction_reply = reply.value
        return Ok(validated)
    except (ValueError, TypeError, ValidationError):
        return Err(
            ExtractionError(
                "invalid_response",
                "The model returned an invalid structured response.",
                task,
            )
        )
