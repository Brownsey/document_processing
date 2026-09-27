"""Metered provider adapters; no provider SDK escapes this module.

Rates verified 2026-09-26 against the official model pages:
https://developers.openai.com/api/docs/models/gpt-6-luna
https://developers.openai.com/api/docs/models/gpt-6-astra
Costs are estimates, never billing assertions. OpenRouter pricing stays unknown.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote

import jsonschema
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, OpenAI

from agent_pipeline.budget import MoneyBudget, Prices
from agent_pipeline.contracts import (
    ConfigError,
    Err,
    ExecutionLimitExceeded,
    ExtractionError,
    InputError,
    ModelReply,
    Ok,
    PipelineError,
    ProviderError,
    Result,
)

CACHE_VERSION = "provider-v3-chat-approved"
CACHE_TASKS = {
    "extract",
    "extraction",
    "image",
    "ocr",
    "image_extract",
    "extract_image",
    "read_image",
}
RATES = {"gpt-6-luna": (0.10, 0.01, 0.125, 0.50), "gpt-6-astra": (10, 1, 12.5, 50)}


class _SpendLimitError(RuntimeError):
    pass


def _json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _schema_safe(value: Any, root: Any = None) -> bool:
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
            if not _schema_safe(item, root):
                return False
        return True
    return not isinstance(value, list) or all(
        _schema_safe(item, root) for item in value
    )


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make optional Pydantic properties required on the wire, retaining their types."""
    schema = json.loads(_json(schema))

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


def _provider_error(exc: APIError) -> ProviderError:
    if isinstance(exc, APITimeoutError):
        return ProviderError("timeout", "Provider request timed out.", "provider", True)
    if isinstance(exc, APIConnectionError):
        return ProviderError(
            "connection", "Provider connection failed.", "provider", True
        )
    status = exc.status_code if isinstance(exc, APIStatusError) else None
    code = {
        400: "invalid_request",
        401: "authentication",
        403: "permission",
        404: "model_unavailable",
        422: "invalid_request",
        429: "rate_limit",
    }.get(status)
    transient = status in {408, 429} or (status is not None and status >= 500)
    code = code or ("service_unavailable" if transient else "provider_failure")
    return ProviderError(
        code,
        "Provider request failed; inspect the safe error code.",
        "provider",
        transient,
    )


def _usage(raw: dict[str, Any], provider: str) -> dict[str, Any]:
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


def _cost(usage: dict[str, Any], provider: str, model: str, tier: str) -> float | None:
    if provider != "openai" or model not in RATES or tier != "default":
        return None
    counts = [
        usage.get(k)
        for k in (
            "input_tokens",
            "cached_input_tokens",
            "cache_write_tokens",
            "output_tokens",
        )
    ]
    if any(type(n) is not int or n < 0 for n in counts):
        return None
    incoming, cached, written, outgoing = cast(list[int], counts)
    if cached + written > incoming:
        return None
    input_rate, cached_rate, write_rate, output_rate = RATES[model]
    if model == "gpt-6-astra" and incoming > 272_000:
        input_rate, cached_rate, write_rate, output_rate = (
            input_rate * 2,
            cached_rate * 2,
            write_rate * 2,
            output_rate * 1.5,
        )
    return (
        (incoming - cached - written) * input_rate
        + cached * cached_rate
        + written * write_rate
        + outgoing * output_rate
    ) / 1_000_000


class Provider:
    """One transport retry owner and a validated extraction cache.

    Schema validation establishes shape, not factual support. The domain still
    validates provenance and reconciles every extraction, including cache hits.
    Investigation decisions use structured output; application code executes
    the allowlisted tools, rather than giving the provider arbitrary tools.
    """

    def __init__(
        self,
        client: OpenAI,
        settings: dict[str, Any],
        cache_dir: Path | None,
        budget: MoneyBudget | None = None,
    ):
        self._client = client
        self.settings = settings
        self.cache_dir = cache_dir
        self.budget = budget
        self.records: list[dict[str, Any]] = []
        self.fingerprint = _digest(settings)
        self._pending_cache: dict[str, tuple[Path, str]] = {}
        self._accepted_cache: dict[str, str] = {}
        self._deadline: float | None = None

    def set_deadline(self, deadline: float) -> None:
        """Set this sequential run's absolute monotonic deadline."""
        self._deadline = deadline

    def _deadline_error(self) -> Err[PipelineError]:
        return Err(
            ExecutionLimitExceeded(
                "run_timeout", "The run deadline was reached.", "provider"
            )
        )

    def approve_cache(self, reply: ModelReply) -> bool:
        """Persist only this exact reply after the caller validates its meaning."""
        if not reply.cache_key or reply.outcome != "success":
            return False
        serialized = _json(asdict(reply))
        if self._accepted_cache.get(reply.cache_key) == serialized:
            return True
        pending = self._pending_cache.get(reply.cache_key)
        if not pending or pending[1] != serialized:
            return False
        path, _ = pending
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                _json({**asdict(reply), "accepted": True}), encoding="utf-8"
            )
        except OSError:
            return False
        self._accepted_cache[reply.cache_key] = serialized
        del self._pending_cache[reply.cache_key]
        return True

    def _record(
        self,
        task: str,
        attempt: int,
        started: float,
        outcome: str,
        usage: dict[str, Any],
        model: str,
        tier: str,
    ) -> None:
        cost = (
            0.0
            if outcome == "cache_hit"
            else _cost(usage, self.settings["provider"], model, tier)
        )
        self.records.append(
            {
                "task": task,
                "provider": self.settings["provider"],
                "model": model,
                "tier": tier,
                "attempt": attempt,
                "outcome": outcome,
                "usage": usage,
                "latency_seconds": round(time.monotonic() - started, 6),
                "estimated_cost_usd": cost,
                "cost_status": "not_billed"
                if outcome == "cache_hit"
                else "unknown"
                if cost is None
                else "estimated",
                "pricing_source": f"https://developers.openai.com/api/docs/models/{model}"
                if model in RATES
                else None,
                "pricing_verified_on": "2026-09-26" if model in RATES else None,
            }
        )

    def complete(
        self,
        *,
        task: str,
        instructions: str,
        context: dict[str, Any],
        schema: dict[str, Any] | None = None,
        image_path: Path | None = None,
    ) -> Result[ModelReply, PipelineError]:
        if self._deadline is not None and time.monotonic() >= self._deadline:
            return self._deadline_error()
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
            serialized = _json(context)
            if schema is not None:
                if (
                    not isinstance(schema, dict)
                    or schema.get("type") != "object"
                    or not _schema_safe(schema)
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
                InputError(
                    "unreadable_image", "Image input could not be read.", "provider"
                )
            )
        except (ValueError, TypeError, jsonschema.SchemaError):
            return Err(
                ConfigError(
                    "invalid_input",
                    "Model input or schema could not be read or validated.",
                    "provider",
                )
            )
        key = _digest(
            {
                "version": CACHE_VERSION,
                "settings": self.settings,
                "code": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "task": task,
                "instructions": instructions,
                "context": context,
                "schema": schema,
                "image": hashlib.sha256(image_bytes).hexdigest()
                if image_bytes
                else None,
            }
        )
        cache = (
            self.cache_dir / f"{key}.json"
            if self.cache_dir and schema and task in CACHE_TASKS
            else None
        )
        if cache and cache.exists():
            try:
                cached = json.loads(cache.read_text(encoding="utf-8"))
                if (
                    not isinstance(cached, dict)
                    or cached.pop("accepted", False) is not True
                    or cached.get("outcome") != "success"
                    or cached.get("cache_key") != key
                ):
                    raise ValueError("Unapproved cache entry")
                jsonschema.validate(cached["data"], schema)
                if cached["text"] != _json(cached["data"]):
                    raise ValueError("inconsistent cache")
                reply = ModelReply(**cached)
                self._accepted_cache[key] = _json(cached)
                self._record(
                    task, 0, time.monotonic(), "cache_hit", {}, reply.model, reply.tier
                )
                return Ok(reply)
            except (
                OSError,
                ValueError,
                TypeError,
                KeyError,
                jsonschema.ValidationError,
            ):
                pass  # Corrupt/stale entries are misses, never usable evidence.
        content: list[dict[str, Any]] = [{"type": "input_text", "text": serialized}]
        if image_bytes:
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}",
                }
            )
        model, tier = self.settings["model"], self.settings["tier"]
        for attempt in range(1, self.settings["max_retries"] + 2):
            started = time.monotonic()
            timeout = self.settings["timeout"]
            if self._deadline is not None:
                timeout = min(timeout, self._deadline - started)
                if timeout <= 0:
                    return self._deadline_error()
            try:
                raw = self._request(instructions, content, schema, timeout)
            except _SpendLimitError:
                self._record(task, attempt, started, "spend_limit", {}, model, tier)
                return Err(
                    ExecutionLimitExceeded(
                        "spend_limit",
                        "The local model spend limit was reached.",
                        "provider",
                    )
                )
            except APIError as exc:
                error = _provider_error(exc)
                self._record(task, attempt, started, error.code, {}, model, tier)
                if (
                    error.retryable
                    and self._deadline is not None
                    and time.monotonic() >= self._deadline
                ):
                    return self._deadline_error()
                if error.retryable and attempt <= self.settings["max_retries"]:
                    delay = min(2 ** (attempt - 1), 4)
                    if (
                        self._deadline is not None
                        and time.monotonic() + delay >= self._deadline
                    ):
                        return self._deadline_error()
                    time.sleep(delay)
                    continue
                return Err(error)
            actual_model, actual_tier = model, tier
            parsed: Result[ModelReply, PipelineError]
            try:
                usage = _usage(raw, self.settings["provider"])
                reported_model = raw.get("model")
                if isinstance(reported_model, str) and (
                    reported_model == model or reported_model.startswith(model + "-")
                ):
                    actual_model = reported_model
                elif reported_model:
                    actual_model = "unknown"
                reported_tier = raw.get("service_tier")
                if reported_tier in {"default", "flex", "priority", "auto"}:
                    actual_tier = reported_tier
                parsed = self._parse(raw, schema, usage, actual_model, actual_tier)
            except (ValueError, TypeError, AttributeError):
                usage = {}
                parsed = Err(
                    ExtractionError(
                        "invalid_response",
                        "Provider output failed validation.",
                        "provider",
                    )
                )
            if self._deadline is not None and time.monotonic() >= self._deadline:
                parsed = self._deadline_error()
            self._record(
                task,
                attempt,
                started,
                parsed.error.code if isinstance(parsed, Err) else "success",
                usage,
                actual_model,
                actual_tier,
            )
            if isinstance(parsed, Ok) and cache:
                parsed = Ok(replace(parsed.value, cache_key=key))
                self._pending_cache[key] = (cache, _json(asdict(parsed.value)))
            return parsed
        raise AssertionError("Retry loop must return")

    def _request(
        self,
        instructions: str,
        content: list[dict[str, Any]],
        schema: dict[str, Any] | None,
        timeout: float,
    ) -> dict[str, Any]:
        options: dict[str, Any] = {"model": self.settings["model"], "timeout": timeout}
        chat_content = [
            {"type": "text", "text": part["text"]}
            if part["type"] == "input_text"
            else {"type": "image_url", "image_url": {"url": part["image_url"]}}
            for part in content
        ]
        options.update(
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": chat_content},
            ],
        )
        if self.settings["provider"] == "openai":
            options.update(
                max_completion_tokens=self.settings["max_output_tokens"],
                reasoning_effort=self.settings["reasoning_effort"],
                service_tier=self.settings["tier"],
                store=False,
            )
        else:
            options["max_tokens"] = self.settings["max_output_tokens"]
        if schema:
            options["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "result",
                    "strict": True,
                    "schema": _strict_schema(schema),
                },
            }
        reservation = None
        price = None
        if self.budget is not None:
            rates = RATES[self.settings["model"]]
            price = Prices(Decimal(str(rates[0])) * 2, Decimal(str(rates[3])) * 2)
            upper_bound = price.cost(
                len(_json(options).encode("utf-8")) + 1000,
                self.settings["max_output_tokens"],
            )
            try:
                reservation = self.budget.reserve(upper_bound)
            except RuntimeError as exc:
                raise _SpendLimitError from exc
        raw = None
        overrun = False
        try:
            raw = self._client.chat.completions.create(**options).model_dump(
                warnings=False
            )
        finally:
            if (
                reservation is not None
                and self.budget is not None
                and price is not None
            ):
                usage = raw.get("usage") if raw else None
                actual = None
                if (
                    isinstance(usage, dict)
                    and type(usage.get("prompt_tokens")) is int
                    and type(usage.get("completion_tokens")) is int
                    and usage["prompt_tokens"] >= 0
                    and usage["completion_tokens"] >= 0
                ):
                    actual = price.cost(
                        usage["prompt_tokens"], usage["completion_tokens"]
                    )
                overrun = self.budget.settle(reservation, actual)
        if overrun:
            raise _SpendLimitError
        return raw

    def _parse(
        self,
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
                text = _json(data)
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


def create_provider(
    provider: str = "openai",
    model: str = "gpt-6-luna",
    *,
    cache_dir: Path | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float = 90,
    max_retries: int = 2,
    max_output_tokens: int = 20_000,
    tier: str = "default",
    reasoning_effort: str = "low",
    cap_usd: Decimal | None = None,
    ledger_path: Path | None = None,
    **extra: Any,
) -> Result[Provider, PipelineError]:
    """Validate local settings then construct an SDK client without network I/O."""
    if (
        provider not in {"openai", "openrouter"}
        or not isinstance(model, str)
        or not model.strip()
        or extra
        or type(timeout) not in {int, float}
        or not 0 < timeout <= 600
        or type(max_retries) is not int
        or not 0 <= max_retries <= 5
        or type(max_output_tokens) is not int
        or not 1 <= max_output_tokens <= 128_000
        or tier != "default"
        or reasoning_effort not in {"none", "low", "medium", "high", "xhigh", "max"}
        or (model == "gpt-6-astra" and reasoning_effort == "none")
        or (
            cap_usd is not None
            and (
                not isinstance(cap_usd, Decimal)
                or not cap_usd.is_finite()
                or cap_usd <= 0
                or provider != "openai"
                or model not in RATES
            )
        )
    ):
        return Err(
            ConfigError(
                "invalid_provider_settings",
                "Provider settings are invalid or unsupported.",
                "provider",
            )
        )
    if provider == "openai":
        if base_url and base_url.rstrip("/") != "https://api.openai.com/v1":
            return Err(
                ConfigError(
                    "invalid_endpoint",
                    "OpenAI requires its official API endpoint.",
                    "provider",
                )
            )
        base_url = "https://api.openai.com/v1"
    elif base_url not in {
        "https://openrouter.ai/api/v1",
        "https://openrouter.ai/api/v1/",
    }:
        return Err(
            ConfigError(
                "invalid_endpoint",
                "Explicit OpenRouter API endpoint is required.",
                "provider",
            )
        )
    credential = api_key or os.getenv(
        "OPENAI_API_KEY" if provider == "openai" else "OPENROUTER_API_KEY"
    )
    if not credential or not credential.strip():
        return Err(
            ConfigError(
                "missing_credentials", "Provider credentials are missing.", "provider"
            )
        )
    settings = {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "timeout": timeout,
        "max_retries": max_retries,
        "max_output_tokens": max_output_tokens,
        "tier": tier,
        "reasoning_effort": reasoning_effort,
        "cache_version": CACHE_VERSION,
    }
    try:
        client = OpenAI(
            api_key=credential, base_url=base_url, timeout=timeout, max_retries=0
        )
        budget = (
            MoneyBudget(ledger_path or Path(".local/paid-budget.sqlite3"), cap_usd)
            if cap_usd is not None
            else None
        )
    except (APIError, ValueError, TypeError):
        return Err(
            ConfigError(
                "client_setup", "Provider client could not be configured.", "provider"
            )
        )
    return Ok(
        Provider(client, settings, Path(cache_dir) if cache_dir else None, budget)
    )
