"""Metered provider transport, validated extraction caching, and client setup."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import jsonschema
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, OpenAI

from agent_pipeline.adapters.provider_payloads import (
    canonical_json,
    normalize_usage,
    parse_reply,
    payload_digest,
    prepare_input,
    strict_schema,
)
from agent_pipeline.contracts import (
    ConfigError,
    Err,
    ExecutionLimitExceeded,
    ExtractionError,
    ModelReply,
    Ok,
    PipelineError,
    ProviderError,
    Result,
)

CACHE_VERSION = "provider-v4-chat-reasoning"
CACHE_TASKS = {"extract", "read_image"}


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
    ):
        self._client = client
        self.settings = settings
        self.cache_dir = cache_dir
        self.records: list[dict[str, Any]] = []
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
        serialized = canonical_json(asdict(reply))
        if self._accepted_cache.get(reply.cache_key) == serialized:
            return True
        pending = self._pending_cache.get(reply.cache_key)
        if not pending or pending[1] != serialized:
            return False
        path, _ = pending
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                canonical_json({**asdict(reply), "accepted": True}), encoding="utf-8"
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
        prepared = prepare_input(task, instructions, context, schema, image_path)
        if isinstance(prepared, Err):
            return prepared
        content, image_bytes = prepared.value
        key = payload_digest(
            {
                "version": CACHE_VERSION,
                "settings": self.settings,
                "code": {
                    name: hashlib.sha256(
                        Path(__file__).with_name(name).read_bytes()
                    ).hexdigest()
                    for name in ("providers.py", "provider_payloads.py")
                },
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
        cached = self._cached_reply(task, key, cache, schema)
        if cached is not None:
            return Ok(cached)
        return self._complete_request(task, instructions, content, schema, key, cache)

    def _cached_reply(
        self,
        task: str,
        key: str,
        cache: Path | None,
        schema: dict[str, Any] | None,
    ) -> ModelReply | None:
        """Load only accepted, schema-valid entries consistent with their text."""
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
                if cached["text"] != canonical_json(cached["data"]):
                    raise ValueError("inconsistent cache")
                reply = ModelReply(**cached)
                self._accepted_cache[key] = canonical_json(cached)
                self._record(
                    task, 0, time.monotonic(), "cache_hit", {}, reply.model, reply.tier
                )
                return reply
            except (
                OSError,
                ValueError,
                TypeError,
                KeyError,
                jsonschema.ValidationError,
            ):
                pass  # Corrupt/stale entries are misses, never usable evidence.
        return None

    def _complete_request(
        self,
        task: str,
        instructions: str,
        content: list[dict[str, Any]],
        schema: dict[str, Any] | None,
        key: str,
        cache: Path | None,
    ) -> Result[ModelReply, PipelineError]:
        """Own retries, deadline checks, metering, and pending cache admission."""
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
                usage = normalize_usage(raw)
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
                parsed = parse_reply(raw, schema, usage, actual_model, actual_tier)
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
                self._pending_cache[key] = (cache, canonical_json(asdict(parsed.value)))
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
        options.update(
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": content},
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
            options["extra_body"] = {
                "reasoning": {"effort": self.settings["reasoning_effort"]}
            }
        if schema:
            options["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "result",
                    "strict": True,
                    "schema": strict_schema(schema),
                },
            }
        return self._client.chat.completions.create(**options).model_dump(
            warnings=False
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
    reasoning_effort: str = "medium",
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
    except (APIError, ValueError, TypeError):
        return Err(
            ConfigError(
                "client_setup", "Provider client could not be configured.", "provider"
            )
        )
    return Ok(Provider(client, settings, Path(cache_dir) if cache_dir else None))
