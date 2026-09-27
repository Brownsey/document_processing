"""Validate provider inputs and replies, and normalize token usage."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import jsonschema

from agent_pipeline.contracts import (
    ConfigError,
    Err,
    ExtractionError,
    InputError,
    ModelReply,
    Ok,
    PipelineError,
    Result,
)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def payload_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def schema_is_safe(value: Any, root: Any = None) -> bool:
    """Never let JSON Schema validation resolve external resources."""
    root = value if root is None else root
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"$id", "$dynamicRef"}:
                return False
            if key == "$ref":
                if not isinstance(item, str) or not (
                    item == "#" or item.startswith("#/")
                ):
                    return False
                target = root
                try:
                    for part in unquote(item[2:]).split("/") if item != "#" else []:
                        part = part.replace("~1", "/").replace("~0", "~")
                        target = (
                            target[int(part)]
                            if isinstance(target, list)
                            else target[part]
                        )
                    if not isinstance(target, (dict, bool)):
                        return False
                except (KeyError, IndexError, TypeError, ValueError):
                    return False
            if not schema_is_safe(item, root):
                return False
        return True
    return not isinstance(value, list) or all(
        schema_is_safe(item, root) for item in value
    )


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make optional Pydantic properties required on the wire, retaining their types."""
    schema = json.loads(canonical_json(schema))

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            # Pydantic's Decimal regex uses lookahead, unsupported by the API.
            # Keep the original schema for local validation of the reply.
            if node.get("pattern") == r"^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$":
                node["pattern"] = r"^[+-]?(\d+(\.\d*)?|\.\d+)$"
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)
    return schema


def normalize_usage(raw: dict[str, Any]) -> dict[str, Any]:
    usage = raw.get("usage") or {}
    if not isinstance(usage, dict):
        raise ValueError("Invalid usage shape")
    input_key, output_key = "prompt_tokens", "completion_tokens"
    details = usage.get("prompt_tokens_details") or {}
    output_details = usage.get("completion_tokens_details") or {}
    if not isinstance(details, dict) or not isinstance(output_details, dict):
        raise ValueError("Invalid usage details")
    normalized = {
        "input_tokens": usage.get(input_key),
        "output_tokens": usage.get(output_key),
        "cached_input_tokens": details.get("cached_tokens", 0),
        "cache_write_tokens": details.get(
            "cache_write_tokens", details.get("cache_creation_tokens", 0)
        ),
        "reasoning_tokens": output_details.get("reasoning_tokens", 0),
    }
    if any(
        value is not None and (type(value) is not int or value < 0)
        for value in normalized.values()
    ):
        raise ValueError("Invalid token counts")
    return normalized


def parse_reply(
    raw: dict[str, Any],
    schema: dict[str, Any] | None,
    usage: dict[str, Any],
    model: str,
    tier: str,
) -> Result[ModelReply, PipelineError]:
    try:
        choice = raw["choices"][0]
        if choice.get("finish_reason") != "stop":
            return Err(
                ExtractionError(
                    "incomplete_response",
                    "Provider output is incomplete.",
                    "provider",
                )
            )
        refused = bool(choice["message"].get("refusal"))
        text = choice["message"].get("content") or ""
        if refused or not text.strip():
            return Err(
                ExtractionError(
                    "refused_or_empty",
                    "Provider output was refused or empty.",
                    "provider",
                )
            )
        data = json.loads(text) if schema else None
        if schema:
            jsonschema.validate(data, schema)
            text = canonical_json(data)
        return Ok(ModelReply(text, data, usage, model, tier))
    except (
        AttributeError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        jsonschema.ValidationError,
    ):
        return Err(
            ExtractionError(
                "invalid_response",
                "Provider output failed schema validation.",
                "provider",
            )
        )


def prepare_input(
    task: str,
    instructions: str,
    context: dict[str, Any],
    schema: dict[str, Any] | None,
    image_path: Path | None,
) -> Result[tuple[list[dict[str, Any]], bytes | None], PipelineError]:
    """Validate local data before cache lookup or provider transport."""
    image_bytes = None
    try:
        if (
            not isinstance(context, dict)
            or not task
            or not isinstance(instructions, str)
        ):
            return Err(
                ConfigError(
                    "invalid_request", "Invalid model request settings.", "provider"
                )
            )
        serialized = canonical_json(context)
        if schema is not None:
            if (
                not isinstance(schema, dict)
                or schema.get("type") != "object"
                or not schema_is_safe(schema)
            ):
                return Err(
                    ConfigError(
                        "invalid_schema",
                        "A local object schema is required.",
                        "provider",
                    )
                )
            jsonschema.Draft202012Validator.check_schema(schema)
        if image_path is not None:
            if image_path.stat().st_size > 20 * 1024 * 1024:
                return Err(
                    InputError(
                        "image_too_large",
                        "Image exceeds the input size limit.",
                        "provider",
                    )
                )
            image_bytes = image_path.read_bytes()
            mime = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
                ".gif": "image/gif",
            }.get(image_path.suffix.lower())
            if not mime or not image_bytes:
                return Err(
                    InputError(
                        "invalid_image",
                        "Unsupported or empty image input.",
                        "provider",
                    )
                )
    except OSError:
        return Err(
            InputError("unreadable_image", "Image input could not be read.", "provider")
        )
    except (ValueError, TypeError, jsonschema.SchemaError):
        return Err(
            ConfigError(
                "invalid_input",
                "Model input or schema could not be read or validated.",
                "provider",
            )
        )
    content: list[dict[str, Any]] = [{"type": "text", "text": serialized}]
    if image_bytes:
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
                },
            }
        )
    return Ok((content, image_bytes))
