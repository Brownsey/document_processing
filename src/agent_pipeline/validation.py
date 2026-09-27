"""Local report contracts; model support review complements these hard checks."""

import re

FCA_LINE = "This firm is authorised and regulated by the Financial Conduct Authority."
RISK_WARNING = (
    "The value of investments can fall as well as rise and you may get back less than you invest. "
    "Past performance is not a guide to future returns."
)
SHAPES = {"phrase", "paragraph", "table", "static"}


def validate_slot(
    text: str, kind: str, *, template: str = "", selector: str = ""
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
    if kind == "phrase" and ("\n" in text or re.search(r"[.!?]\s|\.$", text)):
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
    if report.count("# ") != len(expected) + 1:
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
