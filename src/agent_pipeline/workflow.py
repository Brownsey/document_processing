"""Run lifecycle: load evidence, repair drafts, then publish the report and diagnostic."""

import json
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from agent_pipeline.contracts import (
    Err,
    ExecutionLimitExceeded,
    ExtractionError,
    InputError,
    ModelPort,
    Ok,
    PipelineError,
    ReportBlocked,
    Result,
    RunState,
)
from agent_pipeline.pipeline.drafting import build_report
from agent_pipeline.pipeline.extraction import extract_facts
from agent_pipeline.pipeline.prompt_inputs import evidence_context
from agent_pipeline.pipeline.prompting import ModelSession
from agent_pipeline.pipeline.source_review import review_report
from agent_pipeline.pipeline.validation import load_config
from agent_pipeline.reporting import diagnostics


def _generate_with_repairs(config: dict, client_dir: Path, port, state: RunState):
    from agent_pipeline.adapters.evidence import load_sources

    manifest = state.manifest
    loaded = load_sources(client_dir, port)
    if isinstance(loaded, Err):
        return loaded
    bundle = loaded.value
    manifest["sources"] = bundle.inventory
    manifest["fingerprints"]["inputs"] = diagnostics.hash_value(bundle.inventory)
    context = evidence_context(bundle) | {
        "fingerprints": manifest["fingerprints"],
        "client_scope": str(client_dir.resolve()),
    }
    manifest["repair_counts"] = {"facts": 0, "narrative": 0}
    manifest["repair_history"] = []
    fixed_facts = None
    feedback = None
    # At most two repairs per category, all sharing the same run/time/call budget.
    for _ in range(5):
        state.blocked_report = None
        manifest["inclusion"] = []
        result = _draft_once(
            config, bundle, port, state, context, fixed_facts, feedback
        )
        feedback = state.repair_feedback
        state.repair_feedback = None
        if (
            isinstance(result, Err)
            and isinstance(result.error, ExtractionError)
            and result.error.stage == "reconcile"
            and manifest["repair_counts"]["facts"] < 2
        ):
            feedback = [
                {
                    "code": result.error.code,
                    "message": result.error.message,
                    "details": result.error.details,
                }
            ]
            diagnostics.record_repair(state, "facts", asdict(result.error))
            fixed_facts = None
            continue
        if not isinstance(result, Err) or result.error.code != "unsupported_report":
            return result
        kind = result.error.details.get("issue_kind")
        if kind not in {"facts", "narrative"} or manifest["repair_counts"][kind] >= 2:
            return result
        diagnostics.record_repair(
            state, kind, asdict(result.error), state.blocked_report or ""
        )
        fixed_facts = state.facts if kind == "narrative" else None
    return result


def _draft_once(config, bundle, port, state: RunState, context, fixed_facts, feedback):
    manifest = state.manifest
    if fixed_facts is None:
        extracted = extract_facts(config, bundle, port, state, context, feedback)
        if isinstance(extracted, Err):
            return extracted
        facts = extracted.value
    else:
        facts = fixed_facts
    state.facts = facts
    manifest["review_items"] = facts.get("review_items", [])
    if not facts.get("requested_account_ids"):
        return Err(
            ReportBlocked(
                "missing_scope",
                "The report has no confirmed account scope.",
                "reconcile",
            )
        )
    built = build_report(
        config, bundle, port, state, feedback if fixed_facts is not None else None
    )
    if isinstance(built, Err):
        return built
    report, narratives = built.value
    return review_report(config, context, port, state, report, narratives)


def run_generation(
    *,
    client_dir: Path,
    config_path: Path,
    output_dir: Path,
    provider: ModelPort,
    run_timeout: float = 300,
    max_calls: int = 40,
    tone_of_voice: str | None = None,
    adviser_confirmations: str | None = None,
    publish_anyway: bool = False,
) -> Result[dict, PipelineError]:
    """Write diagnostics last; only an explicit override releases a rejected draft."""
    report_path = output_dir / f"{client_dir.name}.md"
    manifest_path = output_dir / f"{client_dir.name}.manifest.json"
    usage_start = len(diagnostics.usage_records(provider))
    settings = getattr(provider, "settings", {})
    manifest = {
        "run_id": uuid4().hex,
        "status": "running",
        "publish_anyway": publish_anyway,
        "published_anyway": False,
        "client": client_dir.name,
        "output_path": str(report_path.resolve()),
        "sources": [],
        "review_items": [],
        "validation": {"passed": False, "checks": []},
        "usage": [],
        "fingerprints": {},
        "diagnostics": [],
        "investigation_trace": [],
        "inclusion": [],
        "model": settings.get("model", getattr(provider, "model", "unknown")),
        "settings": settings,
        "execution_limits": {"timeout": run_timeout, "max_calls": max_calls},
    }
    state = RunState(manifest=manifest)
    result: Result[str, PipelineError] | None = None
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path.unlink(missing_ok=True)
        diagnostics.atomic_write(
            manifest_path, json.dumps(state.snapshot(), indent=2, default=str)
        )
        loaded = load_config(
            config_path,
            tone_of_voice=tone_of_voice,
            adviser_confirmations=adviser_confirmations,
        )
        if isinstance(loaded, Err):
            result = Err(loaded.error)
        else:
            config = loaded.value
            manifest["adviser_confirmations"] = config.get(
                "adviser_confirmations", "full"
            )
            manifest["review_status"] = config.get("review_status", "unreviewed")
            manifest["fingerprints"] = diagnostics.run_fingerprints(config, settings)
            result = _generate_with_repairs(
                config,
                client_dir,
                ModelSession(
                    provider,
                    timeout=run_timeout,
                    max_calls=max_calls,
                    tone_of_voice=config.get("tone_of_voice", ""),
                ),
                state,
            )
        blocked_report = state.blocked_report
        state.blocked_report = None
        if isinstance(result, Err):
            manifest["status"] = result.error.manifest_status
            manifest["error"] = {
                "type": type(result.error).__name__,
                "code": result.error.code,
                "message": result.error.message,
                "stage": result.error.stage,
                "details": result.error.details,
            }
            if (
                publish_anyway
                and isinstance(result.error, ReportBlocked)
                and result.error.code == "unsupported_report"
                and blocked_report is not None
            ):
                issues = result.error.details.get("issues", [])
                warning = (
                    "> **MANUAL CORRECTION REQUIRED — source-support review failed.**\n"
                    "> Published by explicit override after bounded repair attempts.\n"
                    + "".join(
                        "> " + line + "\n"
                        for issue in issues
                        for line in issue.splitlines()
                    )
                    + "\n"
                )
                result = Ok(warning + blocked_report)
                manifest["published_anyway"] = True
        if isinstance(result, Ok):
            diagnostics.atomic_write(report_path, result.value)
            manifest["status"] = (
                "published_with_issues"
                if manifest["published_anyway"]
                else "needs_review"
            )
            manifest["fingerprints"]["report"] = diagnostics.hash_value(
                result.value.encode()
            )
    except KeyboardInterrupt:
        result = Err(
            ExecutionLimitExceeded(
                "interrupted", "Generation was interrupted.", "generation"
            )
        )
        manifest["status"] = "failed"
        manifest["error"] = {
            "code": "interrupted",
            "message": "Generation was interrupted.",
        }
    except Exception:
        # The application boundary redacts unexpected failures; core services return typed errors.
        result = Err(
            InputError(
                "unexpected_failure",
                "Generation failed unexpectedly; no report was released.",
                "generation",
            )
        )
        manifest["status"] = "failed"
        manifest["error"] = {
            "code": "unexpected_failure",
            "message": "Generation failed unexpectedly.",
        }
    finally:
        state.blocked_report = None
        manifest["usage"] = diagnostics.usage_records(provider)[usage_start:]
        try:
            if manifest["status"] not in {"needs_review", "published_with_issues"}:
                report_path.unlink(missing_ok=True)
        except OSError:
            result = Err(
                InputError(
                    "output_write_failed",
                    "Unable to remove the previous report; it must not be used for this run.",
                    "output",
                )
            )
            manifest["status"] = "failed"
            manifest["error"] = asdict(result.error)
        try:
            diagnostics.atomic_write(
                manifest_path, json.dumps(state.snapshot(), indent=2, default=str)
            )
        except OSError:
            with suppress(OSError):
                report_path.unlink(missing_ok=True)
            result = Err(
                InputError(
                    "output_write_failed",
                    "Unable to write the run diagnostic.",
                    "output",
                )
            )
    if isinstance(result, Err):
        return result
    return Ok(state.snapshot())
