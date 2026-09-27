"""Check report sections, required wording, and introduction scope."""

import re
from collections.abc import Callable

from .matching import _contains, money_values

FCA = "This firm is authorised and regulated by the Financial Conduct Authority."

RISK = "The value of investments can fall as well as rise and you may get back less than you invest. Past performance is not a guide to future returns."


def check_structure(
    report: str,
    expected: dict,
    config: dict | None,
    check: Callable[[str, str, object], None],
) -> dict[str, str]:
    chunks = re.split(r"^##\s+(.+?)\s*$", report, flags=re.M)
    headings = chunks[1::2]
    bodies = chunks[2::2]
    sections = dict(zip(headings, bodies))
    role_titles = {
        "introduction": "Introduction",
        "background_objectives": "Background & Objectives",
        "recommendations": "Recommendations",
        "tax_implications": "Tax Implications",
        "fees_charges": "Fees & Charges",
        "conclusion": "Conclusion",
    }
    if config:
        role_titles.update({s["id"]: s["title"] for s in config["sections"]})
        wanted = [
            s["title"]
            for s in config["sections"]
            if s["id"] != "tax_implications" or expected["tax"]
        ]
    else:
        wanted = [
            title
            for role, title in role_titles.items()
            if role != "tax_implications" or expected["tax"]
        ]
    check("structure", "section order and counts", headings == wanted)
    intro = sections.get(role_titles["introduction"], "")
    background = sections.get(role_titles["background_objectives"], "")
    conclusion = sections.get(role_titles["conclusion"], "")
    check(
        "structure",
        "FCA exact once in introduction",
        report.count(FCA) == 1 and FCA in intro,
    )
    flat = re.sub(r"\s+", " ", report)
    check(
        "structure",
        "risk warning exact once in conclusion",
        flat.count(RISK) == 1 and RISK in re.sub(r"\s+", " ", conclusion),
    )
    check(
        "structure",
        "closing invitation",
        bool(re.search(r"proceed|let us know", conclusion, re.I)),
    )
    check("structure", "introduction has no amounts", not money_values(intro))
    for account in expected["accounts"]:
        check(
            "accounts",
            f"introduction scope {account['id']}",
            _contains(intro, account["aliases"] + account.get("scope_aliases", [])),
        )
    prose_background = "\n".join(
        line for line in background.splitlines() if not line.lstrip().startswith("|")
    )
    check(
        "structure",
        "background has no transaction amounts",
        not money_values(prose_background),
    )
    check(
        "structure",
        "no invented editorial review tasks",
        not re.search(
            r"(?:insert|add|supply|rewrite|remove|duplicate).{0,45}(?:FCA|risk warning|section|heading)|(?:FCA|risk warning).{0,30}(?:insert|missing)",
            report,
            re.I,
        ),
    )
    return {role: sections.get(title, "") for role, title in role_titles.items()}
