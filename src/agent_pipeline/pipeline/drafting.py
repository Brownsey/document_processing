"""Build report sections and validate slots in the assembled draft."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from agent_pipeline.contracts import Err, ExtractionError, Ok, ReportBlocked, RunState
from agent_pipeline.pipeline.prompt_inputs import select_facts
from agent_pipeline.pipeline.prompting import request_structured
from agent_pipeline.pipeline.validation import validate_assembly, validate_slot
from agent_pipeline.reporting import diagnostics
from agent_pipeline.reporting.charges import taxable_disposals
from agent_pipeline.reporting.rendering import render_slot
from document_formatter.formatting import format_document


class Inclusion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["include", "omit", "unresolved"]
    evidence_ids: list[str]
    reason: str


def _slot_shape(spec: dict, name: str, template: str) -> str:
    if "output_type" in spec:
        return spec["output_type"]
    if "renderer" in spec:
        return {"scope": "phrase", "holdings": "table"}.get(spec["renderer"], "static")
    if re.search(r"\b(?:markdown\s+)?table\b", spec.get("prompt", ""), re.I):
        return "table"
    line = next(line for line in template.splitlines() if f"<<{name}>>" in line)
    return "phrase" if line.strip() != f"<<{name}>>" else "paragraph"


def _include_section(section, config, facts, port, known_ids, manifest):
    if section.get("inclusion_selector") == "taxable_disposals":
        decision = "include" if taxable_disposals(facts) else "omit"
        manifest["inclusion"].append(
            {
                "section": section["id"],
                "decision": decision,
                "reason": "validated taxable disposal actions",
            }
        )
        if decision == "omit":
            return Ok(False)
    elif section.get("use_if", "always") != "always":
        verdict = request_structured(
            port,
            schema=Inclusion,
            task="inclusion",
            instructions=config.get(
                "inclusion_prompt",
                "Decide include, omit or unresolved using the rule and validated facts; provide supporting evidence IDs. Do not silently omit an unresolved section.",
            ),
            context={"rule": section["use_if"], "facts": facts},
        )
        if isinstance(verdict, Err):
            return verdict
        value = verdict.value
        if (
            value.decision not in {"include", "omit", "unresolved"}
            or not value.evidence_ids
            or not set(value.evidence_ids) <= known_ids
        ):
            return Err(
                ExtractionError(
                    "invalid_inclusion",
                    "Section inclusion lacks valid supporting evidence.",
                    "inclusion",
                )
            )
        manifest["inclusion"].append({"section": section["id"], **value.model_dump()})
        if value.decision == "unresolved":
            return Err(
                ReportBlocked(
                    "unresolved_inclusion",
                    "A section inclusion rule remains unresolved.",
                    "inclusion",
                    details={"section": section["id"]},
                )
            )
        if value.decision == "omit":
            return Ok(False)

    return Ok(True)


def _static_slot(name, spec, shape, section, facts, config):
    text = render_slot(spec["renderer"], facts, config)
    defects = (
        []
        if spec["renderer"] == "adviser_queries" and not text
        else validate_slot(
            text,
            shape,
            template=section["template"],
            literal_phrases=[
                value
                for account in facts.get("accounts", [])
                + facts.get("planned_accounts", [])
                for value in [
                    account.get("platform", ""),
                    account.get("account_type", ""),
                    account["account_id"],
                    *account.get("owners", []),
                ]
            ]
            if spec["renderer"] == "scope"
            else (),
        )
    )
    if defects:
        return Err(
            ReportBlocked(
                "invalid_static_slot",
                "Deterministic slot violated its output contract.",
                "render",
                details={"slot": name, "checks": defects},
            )
        )

    return Ok(text)


def _write_slot(name, spec, shape, section, config, port, state: RunState, feedback):
    manifest = state.manifest
    facts = state.facts
    selector = spec.get("selector", "all")
    if "selector" not in spec:
        manifest["diagnostics"].append({"code": "legacy_full_facts", "slot": name})
    selected = select_facts(selector, facts)
    narrative_context = {
        "facts": selected,
        "template": section["template"],
        "slot": name,
        "output_type": shape,
    }
    if feedback:
        narrative_context["repair"] = {
            "issues": feedback,
            "fixed_facts": True,
        }
    for attempt in range(3):
        reply = port.complete(
            task="write",
            instructions=config.get("global_instructions", "")
            + "\n\n"
            + spec["prompt"],
            context=narrative_context,
        )
        if isinstance(reply, Err):
            return reply
        text = reply.value.text.strip()
        defects = validate_slot(
            text, shape, template=section["template"], selector=selector
        )
        if not defects:
            break
        if manifest["repair_counts"]["narrative"] >= 2:
            break
        diagnostics.record_repair(
            state,
            "narrative",
            {"stage": "write", "slot": name, "checks": defects},
            text,
        )
        narrative_context["repair"] = {
            "defects": defects,
            "previous": text,
            "fixed_facts": True,
        }
    if defects:
        return Err(
            ReportBlocked(
                "invalid_narrative_slot",
                "Narrative repairs did not satisfy the slot contract.",
                "write",
                details={"slot": name, "checks": defects},
            )
        )

    return Ok(text)


def build_report(config, bundle, port, state: RunState, feedback):
    manifest = state.manifest
    facts = state.facts
    sections = []
    narratives = []
    known_ids = {b.id for b in bundle.blocks if b.role != "excluded"}
    for section in config["sections"]:
        included = _include_section(section, config, facts, port, known_ids, manifest)
        if isinstance(included, Err):
            return included
        if not included.value:
            continue
        content = section["template"]
        for name, spec in section.get("placeholders", {}).items():
            shape = _slot_shape(spec, name, section["template"])
            if "renderer" in spec:
                result = _static_slot(name, spec, shape, section, facts, config)
            else:
                result = _write_slot(
                    name, spec, shape, section, config, port, state, feedback
                )
            if isinstance(result, Err):
                return result
            text = result.value
            if "renderer" not in spec:
                narratives.append({"slot": name, "text": text})
            content = content.replace(f"<<{name}>>", text)
        sections.append(
            {
                "id": section["id"],
                "title": section["title"],
                "content": content,
            }
        )
    report = format_document(config, sections)
    defects = validate_assembly(config, sections, report)
    if defects:
        return Err(
            ReportBlocked(
                "invalid_report",
                "The assembled report violated its structure contract.",
                "validate",
                details={"checks": defects},
            )
        )
    return Ok((report, narratives))
