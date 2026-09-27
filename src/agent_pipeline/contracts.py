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


@dataclass
class RunState:
    """Working facts and repair state, separate from persisted run metadata."""

    manifest: dict[str, Any]
    facts: dict[str, Any] = field(default_factory=dict)
    repair_feedback: list[str] | list[dict[str, Any]] | None = None
    blocked_report: str | None = None

    def snapshot(self) -> dict[str, Any]:
        """Keep the existing manifest format without temporary working fields."""
        return self.manifest | {"facts": self.facts}


@dataclass(frozen=True)
class PipelineError:
    code: str
    message: str
    stage: str = ""
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def manifest_status(self) -> str:
        return "failed"


@dataclass(frozen=True)
class ConfigError(PipelineError):
    stage: str = "config"


@dataclass(frozen=True)
class InputError(PipelineError):
    stage: str = "input"


@dataclass(frozen=True)
class ProviderError(PipelineError):
    stage: str = "provider"


@dataclass(frozen=True)
class ExtractionError(PipelineError):
    stage: str = "extraction"


@dataclass(frozen=True)
class ExecutionLimitExceeded(PipelineError):
    stage: str = "execution"


@dataclass(frozen=True)
class ReportBlocked(PipelineError):
    stage: str = "report"

    @property
    def manifest_status(self) -> str:
        return "blocked"


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
