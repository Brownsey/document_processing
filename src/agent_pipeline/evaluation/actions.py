"""Check that rendered recommendations only contain authorised actions."""

import re
from collections.abc import Callable
from decimal import Decimal

from .matching import SELL, _contains, _normal, _sentences, money_values


def _is_action_claim(sentence: str) -> bool:
    """Identify advice predicates, including modal prose, without treating nouns as orders."""
    verbs = r"transfer\w*|mov(?:e|ing)\w*|top[ -]?up|topping up|contribut(?:e|ing)|sell|disinvest\w*|dispose|gift|donate|open|retain|keep"
    text = _normal(sentence)
    predicate = re.search(r"\b(?:" + verbs + r")\b", text)
    if not predicate:
        return False
    prefix = text[: predicate.start()]
    # A pending confirmation can refer to an agreed top-up as a noun.
    # Later imperative/modal clauses still count, even inside a review marker.
    if re.search(r"\b(?:confirm|verify)\b", prefix) and re.search(
        r"\b(?:before|prior to) implementing the agreed\s+$", prefix
    ):
        return False
    return (
        not prefix.strip(" -*")
        or bool(
            re.search(
                r"\b(?:recommend\w*|should|must|will|please|agree\w*|propos\w*|advise\w*|need to|do not|don't|then)\b",
                prefix,
            )
        )
        or bool(money_values(text) and re.search(r"\b(?:from|to|into)\b", text))
    )


def _action_matches(sentence: str, action: dict) -> bool:
    s = _normal(sentence)
    source = action.get("source", [])
    dest = action.get("destination", [])
    if source and not _contains(s, source):
        return False
    if dest and not _contains(s, dest):
        return False
    if action.get("amount") and Decimal(action["amount"]) not in money_values(s):
        return False
    if re.search(
        r"\b(?:not|never|no|don't)\b.{0,35}\b(?:sell|transfer|invest|top|disinvest|open|establish|creat|contribut|fund|allocat|add|split)\w*\b|not recommended|cancel(?:led)?",
        s,
    ):
        return False
    kind = action["kind"]
    predicate = re.sub(r"\([^)]*\)", "", s)
    if (
        not action.get("amount")
        and money_values(s)
        and (
            kind in {"contribute", "open", "transfer"}
            or action.get("extent") == "partial"
        )
    ):
        return False
    if kind == "sell":
        if not SELL.search(s):
            return False
        full = bool(re.search(r"\b(full|entire\w*|all|whole|fully)\b", s))
        partial = bool(re.search(r"\b(part|partial\w*|portion|some)\b", s))
        return (
            (full and not partial)
            if action.get("extent") == "full"
            else (partial and not full)
        )
    if kind == "retain":
        return bool(re.search(r"\b(retain\w*|keep|leave|unchanged|hold)\b", s))
    lead = r"(?:^[-* ]*|\b(?:recommend(?:ation is)?|propose|advise|agree)(?: that)?(?: you| we)?(?: to)?\s+|\b(?:should|will|must|then|and|please)\s+)"
    if kind == "open":
        return bool(
            re.search(
                lead + r"(?:open(?:ing)?|establish(?:ing)?|creat(?:e|ing))\b",
                predicate,
            )
        )
    if not re.search(
        lead
        + r"(transfer(?:ring|red)?|mov(?:e|ing)|top[ -]?up|topping up|contribut(?:e|ing)|fund(?:ing)?|invest(?:ing)?|allocat(?:e|ing)|add(?:ing)?|split(?:ting)?)\b",
        predicate,
    ):
        return False
    if source and dest:
        # Explicit direction is mandatory. Accept both active and reversed clause order.
        for source_alias in source:
            for destination_alias in dest:
                a = re.escape(_normal(source_alias))
                b = re.escape(_normal(destination_alias))
                forward = rf"\bfrom\b.{{0,35}}{a}.{{0,80}}\b(?:to|into)\b.{{0,35}}{b}"
                backward = rf"{b}.{{0,80}}\bfrom\b.{{0,35}}{a}"
                if re.search(forward, s) or re.search(backward, s):
                    return True
        return False
    return True


def check_actions(
    recommendations: str, expected: dict, check: Callable[[str, str, object], None]
) -> None:
    sentences = _sentences(recommendations)
    for index, action in enumerate(expected["actions"]):
        check(
            "actions",
            f"action {index + 1}: {action['kind']}",
            any(_action_matches(s, action) for s in sentences),
        )
    for sentence in sentences:
        if _is_action_claim(sentence):
            check(
                "actions",
                "every directive is authorised",
                any(
                    _action_matches(sentence, action) for action in expected["actions"]
                ),
            )
    permitted_sales = [a for a in expected["actions"] if a["kind"] == "sell"]
    for sentence in sentences:
        if not SELL.search(sentence) or re.search(
            r"\b(no|not|without)\b.{0,45}(?:sell|sold|sale|dispos|disinvest)|no investments are being sold",
            sentence,
            re.I,
        ):
            continue
        # Require each positively named disposal account to be authorised, not merely one.
        mentioned = [
            a for a in expected["accounts"] if _contains(sentence, a["aliases"])
        ]
        allowed = bool(permitted_sales) and all(
            any(
                _contains(" ".join(a["aliases"]), sale["source"])
                or _contains(sentence, sale["source"])
                and "gia" in _normal(" ".join(a["aliases"]))
                for sale in permitted_sales
            )
            for a in mentioned
        )
        check("actions", "no unauthorised disposal", allowed)
