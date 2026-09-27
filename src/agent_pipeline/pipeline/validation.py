"""Local report contracts; model support review complements these hard checks."""

import json
import re
from pathlib import Path

from agent_pipeline.contracts import ConfigError, Err, Ok, PipelineError, Result

FCA_LINE = "This firm is authorised and regulated by the Financial Conduct Authority."
RISK_WARNING = (
    "The value of investments can fall as well as rise and you may get back less than you invest. "
    "Past performance is not a guide to future returns."
)
SHAPES = {"phrase", "paragraph", "table", "static"}


def validate_slot(
    text: str,
    kind: str,
    *,
    template: str = "",
    selector: str = "",
    literal_phrases: list[str] | tuple[str, ...] = (),
) -> list[str]:
    """Return defects, never convert a generation defect to an adviser review item."""
    errors = []
    if not text.strip():
        errors.append("empty_slot")
    if re.search(r"(?m)^\s*#{1,6}\s|<<[^>]+>>|```", text):
        errors.append("unexpected_structure")
    if re.search(
        r"\b(?:insert|add|include|supply)\b.{0,45}\b(?:FCA|regulatory|risk warning|wording)\b",
        text,
        re.I,
    ):
        errors.append("insertion_instruction")
    if kind == "phrase":
        # Sourced account names may contain punctuation; raw structure still applies.
        phrase = text
        for literal in sorted(set(literal_phrases) - {""}, key=len, reverse=True):
            phrase = phrase.replace(literal, "name")
        if "\n" in text or re.search(r"[.!?]\s|\.$", phrase):
            errors.append("not_phrase")
    if kind in {"phrase", "paragraph"} and re.search(r"(?m)^\s*\||^\s*[-*]\s", text):
        errors.append("unexpected_table_or_list")
    if kind == "paragraph" and "\n\n" in text:
        errors.append("multiple_paragraphs")
    if kind == "table" and not re.search(r"(?m)^\|\s*:?-+", text):
        errors.append("not_table")
    for opening in ("We recommend the following", "Further to our recent discussions"):
        if (
            opening.casefold() in template.casefold()
            and opening.casefold() in text.casefold()
        ):
            errors.append("duplicate_opening")
    if selector in {"introduction", "background", "recommendations", "rationale"}:
        if re.search(
            r"[£$€]|\b\d[\d,.]*\s*(?:%|percent|pounds|GBP|USD|EUR)\b|\d\s*%", text, re.I
        ):
            errors.append("financial_amount_in_narrative")
    if selector in {"recommendations", "rationale"} and re.search(
        r"(?:^|[.!?]\s+)(?:we recommend (?:you )?|you should )?(?:sell|dispose|transfer|contribute|withdraw|top[ -]?up|purchase|invest)\b",
        text,
        re.I,
    ):
        errors.append("financial_action_in_explanation")
    return list(dict.fromkeys(errors))


def validate_assembly(config: dict, sections: list[dict], report: str) -> list[str]:
    expected = [f"## {s['title']}" for s in sections if s.get("title", "").strip()]
    errors = []
    if re.findall(r"(?m)^## .+$", report) != expected:
        errors.append("section_order_or_count")
    if len(re.findall(r"(?m)^\s{0,3}#{1,6}\s", report)) != len(expected) + 1:
        errors.append("unexpected_headings")
    for fixed in (FCA_LINE, config.get("risk_warning", RISK_WARNING)):
        locations = [s.get("id") for s in sections if fixed in s["content"]]
        declared = [
            s["id"]
            for s in config.get("sections", [])
            if fixed in s.get("template", "")
            or (
                fixed == config.get("risk_warning", RISK_WARNING)
                and (
                    any(
                        p.get("renderer") == "risk_warning"
                        for p in s.get("placeholders", {}).values()
                    )
                    or "<<risk_warning>>" in s.get("template", "")
                )
            )
        ]
        if declared and (report.count(fixed) != 1 or locations != declared):
            errors.append("fixed_wording_placement")
    if "<<" in report:
        errors.append("unfilled_slot")
    return errors


SELECTORS = {
    "introduction",
    "background",
    "recommendations",
    "rationale",
    "tax",
    "fees",
    "all",
}
RENDERERS = {
    "scope",
    "holdings",
    "actions",
    "action_plan",
    "adviser_queries",
    "tax",
    "fees",
    "risk_warning",
}


def validate_config(config: dict) -> Result[dict, PipelineError]:
    """Resolve the entire template contract before any provider interaction."""

    def require(condition):
        if not condition:
            raise ValueError("Invalid configuration")

    try:
        require(isinstance(config, dict))
        require(config.get("adviser_confirmations", "full") in {"full", "discrepancy"})
        require(
            isinstance(config.get("document_title"), str)
            and config["document_title"].strip()
            and "\n" not in config["document_title"]
        )
        for key in (
            "tone_of_voice",
            "global_instructions",
            "extraction_prompt",
            "inclusion_prompt",
            "investigation_prompt",
            "validation_prompt",
            "risk_warning",
        ):
            require(
                key not in config
                or isinstance(config[key], str)
                and config[key].strip()
            )
        require(isinstance(config["sections"], list) and config["sections"])
        identifiers = []
        for section in config["sections"]:
            require(isinstance(section, dict))
            require(isinstance(section["id"], str) and section["id"].strip())
            identifiers.append(section["id"])
            require(isinstance(section["title"], str) and "\n" not in section["title"])
            require(isinstance(section.get("use_if", "always"), str))
            require(section.get("inclusion_selector") in {None, "taxable_disposals"})
            template = section["template"]
            slots = re.findall(r"<<([^<>]+)>>", template)
            specs = section.get("placeholders", {})
            require(isinstance(specs, dict))
            require(len(slots) == len(set(slots)) and set(slots) == set(specs))
            for spec in specs.values():
                require(isinstance(spec, dict))
                require(spec.get("output_type", "paragraph") in SHAPES)
                require(spec.get("selector", "all") in SELECTORS)
                if "renderer" in spec:
                    require(spec["renderer"] in RENDERERS)
                else:
                    require(
                        isinstance(spec.get("prompt"), str) and spec["prompt"].strip()
                    )
        require(len(identifiers) == len(set(identifiers)))
    except (AssertionError, KeyError, TypeError, ValueError):
        return Err(
            ConfigError(
                "invalid_config",
                "Invalid section, slot, selector or renderer contract.",
                "config",
            )
        )
    return Ok(config)


def load_config(path: Path, **overrides) -> Result[dict, PipelineError]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Err(
            ConfigError(
                "invalid_config", "Configuration cannot be read as JSON.", "config"
            )
        )
    loaded = validate_config(config)
    for name, value in overrides.items():
        if isinstance(loaded, Err):
            return loaded
        if value is not None:
            loaded = validate_config(loaded.value | {name: value})
    return loaded
