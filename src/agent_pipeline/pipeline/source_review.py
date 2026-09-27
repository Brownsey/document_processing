"""Review the assembled draft against source evidence before approving its cache."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_pipeline.contracts import Err, ExtractionError, Ok, ReportBlocked, RunState
from agent_pipeline.pipeline.prompt_inputs import review_facts
from agent_pipeline.pipeline.prompting import request_structured


class SupportReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supported: bool
    issues: list[str]
    issue_kind: Literal["none", "facts", "narrative", "code"] = Field(
        default="none",
        description="Use code for a fixed template or deterministic rendering defect that re-extraction cannot fix; facts for extraction errors; narrative for generated prose only; none only when supported. Code takes precedence in mixed failures.",
    )


def review_report(config, context, port, state: RunState, report, narratives):
    manifest = state.manifest
    facts = state.facts
    reviewed = request_structured(
        port,
        schema=SupportReview,
        task="validate_support",
        instructions=config.get(
            "validation_prompt",
            "Verify facts and narrative against cited evidence. Reject unsupported claims, numeric inventions, reversed transfers, unauthorised disposals, expanded scope, contradictions, omitted decisions and generation defects disguised as review items. Supported explanations and paraphrases are valid. Return supported and issues; never request missing application-supplied wording.",
        ),
        context={
            **{key: value for key, value in context.items() if key != "repair"},
            "report_stage": "adviser_review_draft",
            "adviser_confirmations": config.get("adviser_confirmations", "full"),
            "facts": review_facts(facts),
            "narratives": narratives,
            "report": report,
        },
    )
    if isinstance(reviewed, Err):
        return reviewed
    if (
        reviewed.value.supported
        and (reviewed.value.issues or reviewed.value.issue_kind != "none")
    ) or (not reviewed.value.supported and reviewed.value.issue_kind == "none"):
        return Err(
            ExtractionError(
                "invalid_response",
                "Support review returned a contradictory verdict.",
                "validate_support",
            )
        )
    if not reviewed.value.supported or reviewed.value.issues:
        state.blocked_report = report
        state.repair_feedback = reviewed.value.issues
        return Err(
            ReportBlocked(
                "unsupported_report",
                "Source support review rejected the draft.",
                "validate",
                details={
                    "issue_count": len(reviewed.value.issues),
                    "issues": reviewed.value.issues,
                    "issue_kind": reviewed.value.issue_kind,
                },
            )
        )
    manifest["validation"] = {
        "passed": True,
        "checks": [
            "config",
            "provenance_reconciliation",
            "slot_shapes",
            "section_order",
            "fixed_wording",
            "source_support",
        ],
    }
    if port.latest_extraction_reply is not None:
        port.approve_cache(port.latest_extraction_reply)
    return Ok(report)
