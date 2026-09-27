"""Text and amount matching shared by independent scoring checks."""

import re
from decimal import Decimal, InvalidOperation
from typing import Any

MONEY = re.compile(
    r"(?:£|GBP\s*)(\d[\d,]*(?:\.\d+)?)(?:\s*(thousand|million|billion|bn|[km]))?(?![\w\d])",
    re.I,
)

SELL = re.compile(
    r"\b(sell|selling|sold|sale|sales|disinvest\w*|dispos\w*|liquidat\w*)\b", re.I
)

REVIEW = re.compile(
    r"confirm|verif|review|unknown|outstanding|not (?:known|available)|to be agreed|subject to",
    re.I,
)


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("’", "'").replace("**", "")).strip().lower()


def _contains(text: str, aliases: list[str], *, prose: bool = False) -> bool:
    for alias in aliases:
        pattern = re.escape(_normal(alias))
        if prose:
            pattern = pattern.replace(r"\ ", "[ -]")
        if re.search(r"(?<!\w)" + pattern + r"(?!\w)", _normal(text)):
            return True
    return False


def money_values(text: str) -> list[Decimal]:
    factors = {
        "k": 1000,
        "thousand": 1000,
        "m": 1000000,
        "million": 1000000,
        "bn": 1000000000,
        "billion": 1000000000,
    }
    return [
        Decimal(m.group(1).replace(",", ""))
        * factors.get((m.group(2) or "").lower(), 1)
        for m in MONEY.finditer(text)
    ]


def _same_amount(actual: Any, expected: Any) -> bool:
    if actual is None or expected is None:
        return actual is expected
    try:
        return Decimal(str(actual)) == Decimal(str(expected))
    except InvalidOperation:
        return False


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[.!?](?=\s|$)|\n|;", text) if s.strip()]
