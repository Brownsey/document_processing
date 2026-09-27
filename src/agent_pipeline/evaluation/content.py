"""Check narrative, funding, reviews, tax, fees, and unsupported content."""

import re
from collections.abc import Callable
from decimal import Decimal

from .matching import REVIEW, _contains, _normal, _same_amount, _sentences, money_values


def _supported_fee_rates(sentence: str, initial_rate: str) -> bool:
    previous = 0
    for rate in re.finditer(
        r"(?<![\d.])(\d+(?:\.\d+)?)\s*(?:%|per cent)", sentence, re.I
    ):
        prefix = _normal(sentence[previous : rate.start()])
        labels = re.findall(r"\b(initial|platform|ongoing)\b", prefix)
        if (
            not labels
            or labels[-1] != "initial"
            or not _same_amount(rate.group(1), initial_rate)
        ):
            return False
        previous = rate.end()
    return True


def check_narrative(
    report: str, expected: dict, check: Callable[[str, str, object], None]
) -> None:
    for alternatives in expected.get("required_concepts", []):
        check(
            "narrative",
            "required: " + alternatives[0],
            _contains(report, alternatives, prose=True),
        )


def check_funding(
    recommendations: str, expected: dict, check: Callable[[str, str, object], None]
) -> None:
    sentences = _sentences(recommendations)
    for funding in expected.get("funding", []):
        for sentence in sentences:
            for clause in re.split(r",\s+|\bbut\b|\bwhereas\b", sentence, flags=re.I):
                if not _contains(clause, funding["labels"]):
                    continue
                available = bool(
                    re.search(
                        r"\b(?:available|investable|invest(?:ing|ed)?|fund(?:ing)?)\b",
                        clause,
                        re.I,
                    )
                )
                excluded = bool(
                    re.search(
                        r"\b(?:exclude\w*|not available|unavailable|cannot|must not|do not|not included|not received|has not been received)\b",
                        clause,
                        re.I,
                    )
                )
                if funding["status"] in {"excluded", "reserved"} and available:
                    check(
                        "funding",
                        "unavailable funds cannot become available: "
                        + funding["labels"][0],
                        excluded,
                    )
                if (
                    funding["status"] == "available"
                    and available
                    and not excluded
                    and money_values(clause)
                ):
                    check(
                        "funding",
                        "available funding amount",
                        money_values(clause) == [Decimal(funding["amount"])],
                    )


def check_review(
    report: str, expected: dict, check: Callable[[str, str, object], None]
) -> None:
    for concepts in expected.get("review_concepts", []):
        check(
            "review",
            "confirmation: " + "/".join(concepts),
            any(
                all(_contains(s, [term, term + "s"]) for term in concepts)
                and REVIEW.search(s)
                for s in _sentences(report)
            ),
        )


def check_tax(
    tax: str, expected: dict, check: Callable[[str, str, object], None]
) -> None:
    if expected["tax"]:
        check(
            "tax",
            "annual exempt amount",
            bool(re.search(r"annual exempt|annual exemption", tax, re.I)),
        )
        check(
            "tax",
            "capital gains human confirmation",
            bool(re.search(r"capital gains|CGT", tax, re.I))
            and bool(REVIEW.search(tax)),
        )
        check(
            "tax",
            "no invented tax amount",
            not money_values(tax)
            and not re.search(r"\d+(?:\.\d+)?\s*(?:%|per cent)", tax),
        )


def check_fees(
    report: str, fees: str, expected: dict, check: Callable[[str, str, object], None]
) -> None:
    rates = re.findall(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(?:%|per cent)", fees, flags=re.I)
    check(
        "fees",
        "sourced initial charge only",
        bool(rates)
        and all(Decimal(r) == Decimal(expected["initial_rate"]) for r in rates),
    )
    check(
        "fees",
        "rate belongs to initial charge",
        any(
            re.search(r"initial", s, re.I)
            and re.search(r"\d+(?:\.\d+)?\s*(?:%|per cent)", s)
            for s in _sentences(fees)
        ),
    )
    check("fees", "no invented cash fee", not money_values(fees))
    for sentence in _sentences(report):
        if re.search(
            r"(?:platform|ongoing|advice).{0,35}(?:charge|fee|rate)", sentence, re.I
        ):
            check(
                "fees",
                "unknown ongoing rates remain unknown",
                _supported_fee_rates(sentence, expected["initial_rate"])
                and not money_values(sentence),
            )


def check_unsupported(
    report: str, expected: dict, check: Callable[[str, str, object], None]
) -> None:
    allowed_values = {Decimal(x) for x in expected.get("allowed_amounts", [])}
    check(
        "unsupported",
        "all monetary amounts source-backed",
        all(v in allowed_values for v in money_values(report)),
    )
    for label in expected.get("forbidden_labels", []):
        check("unsupported", f"no distractor {label}", not _contains(report, [label]))
