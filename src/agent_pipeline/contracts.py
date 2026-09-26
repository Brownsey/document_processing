"""Provider-independent values exchanged at the pipeline boundaries."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, Literal, Protocol, TypeAlias, TypeVar

T = TypeVar("T")
E = TypeVar("E")


@dataclass(frozen=True)
class Ok(Generic[T]):
    value: T


@dataclass(frozen=True)
class Err(Generic[E]):
    error: E


Result: TypeAlias = Ok[T] | Err[E]


@dataclass(frozen=True)
class PipelineError:
    code: str
    message: str
    stage: str = ""
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)


class ConfigError(PipelineError):
    pass


class InputError(PipelineError):
    pass


class ProviderError(PipelineError):
    pass


class ExtractionError(PipelineError):
    pass


class ExecutionLimitExceeded(PipelineError):
    pass


class ReportBlocked(PipelineError):
    pass


@dataclass(frozen=True)
class EvidenceBlock:
    id: str
    source: str
    locator: str
    text: str
    sha256: str
    role: Literal["evidence", "guidance", "excluded"] = "evidence"


@dataclass(frozen=True)
class EvidenceBundle:
    blocks: list[EvidenceBlock]
    inventory: list[dict[str, Any]]
    databases: list[dict[str, Any]]


@dataclass(frozen=True)
class ModelReply:
    text: str
    data: dict[str, Any] | None
    usage: dict[str, Any]
    model: str
    tier: str = "default"
    outcome: str = "success"
    cache_key: str | None = None


class ModelPort(Protocol):
    def complete(
        self,
        *,
        task: str,
        instructions: str,
        context: dict[str, Any],
        schema: dict[str, Any] | None = None,
        image_path: Path | None = None,
    ) -> Result[ModelReply, PipelineError]: ...
