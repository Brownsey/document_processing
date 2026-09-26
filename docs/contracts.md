# V1 module contract

Implementation reference; the assignment and IMPLEMENTATION_PLAN.md remain acceptance criteria.

## Shared types (`agent_pipeline.contracts`)

- Frozen `Ok[T](value)`, `Err[E](error)` and `Result[T, E]` alias.
- `PipelineError(code, message, stage="", retryable=False, details={})`; subclasses `ConfigError`, `InputError`, `ProviderError`, `ExtractionError`, `ExecutionLimitExceeded`, `ReportBlocked`. Messages/details must be safe to display. Preserve errors through services.
- `EvidenceBlock(id, source, locator, text, sha256, role="evidence")`; role evidence/guidance/excluded.
- `EvidenceBundle(blocks, inventory, databases)`; inventory is list of JSON dictionaries, databases are structured JSON sources.
- `ModelReply(text, data, usage, model, tier="default", outcome="success", cache_key=None)`; data is a JSON dict or None; usage is a JSON dict. Optional adapter `approve_cache(reply)` persists only that exact accepted reply after caller validation; the workflow wrapper forwards it.
- `ModelPort.complete(*, task, instructions, context, schema=None, image_path=None) -> Result[ModelReply, PipelineError]`. Context/schema are JSON dicts; image_path is Path or None. All SDK access stays in adapters. No constructor I/O.

## Ownership and interfaces

- Domain owner: contracts.py, domain.py, tests/test_domain.py, tests/test_contracts.py. `CaseFacts` Pydantic model + `reconcile(facts: CaseFacts, bundle: EvidenceBundle) -> Result[CaseFacts, PipelineError]`; publish schema/fields to coordinator promptly. Values use Decimal, effective dates and precision; references use evidence ID and excerpt. No SDK imports.
- Evidence owner: evidence.py, tests/test_evidence.py. `load_sources(client_dir: Path, provider: ModelPort, cache_dir: Path | None = None) -> Result[EvidenceBundle, PipelineError]`. Preserve DOCX order/tables and image evidence. No network except supplied provider. Inputs never mutate.
- Provider owner: providers.py, tests/test_providers.py. `create_provider(provider="openai", model="gpt-6-luna", *, cache_dir=None, **settings) -> Result[ModelPort, PipelineError]`. Use environment credentials privately; explicit OpenRouter selection, no live OpenRouter testing. Provider instance exposes serialisable usage records. Structured Responses API, bounded retry owner, usage accounting, validated cache only. Optional `set_deadline(absolute_monotonic_time)` bounds each attempt and retry to the shared run deadline. No monetary cap.
- Report owner: workflow.py, rendering.py, validation.py, generate.py and their tests. `run_generation(*, client_dir: Path, config_path: Path, output_dir: Path, provider: ModelPort, cache_dir: Path | None = None) -> Result[dict, PipelineError]`. Return a serialisable run manifest with facts, provenance, validation, model/usage/fingerprints and output path. Errors produce diagnostic output and never stale success. Supplied CLI flags remain. Existing formatter stays byte-identical.
- Evaluation owner: evaluation.py, experiments.py and their tests; eval/ source-reviewed expectations, reserved scenarios. Consume run_generation, not a second generation path. Optional MLflow imports. Review-gate automatic optimisation; first round ends with Astra candidate review pending. No historical run files committed.
- Lead owns config/, pyproject.toml, uv.lock, README/DECISIONS/plan, dependency installation, initial/final commits and aggregate verification. No worker commits, branch switches, dependency installs, shared config edits or outside-owned files.

Use task-specific temporary directories. Write meaningful failing tests before implementing. All modules are covered by Ruff, ty and pytest. Return red/green evidence and known gaps. Deadline/bounded calls are allowed; spending has no cap.
