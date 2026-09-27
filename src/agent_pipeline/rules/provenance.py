"""Validate citations, amounts, qualifiers and dates against source evidence."""

import re
from datetime import date
from decimal import Decimal

from agent_pipeline.contracts import (
    Err,
    EvidenceBundle,
    ExtractionError,
    Ok,
    PipelineError,
    Result,
)
from agent_pipeline.rules.models import Account, CaseFacts, FactModel, Fee, SourceRef
from agent_pipeline.rules.review import failure


def normal(text: str) -> str:
    return " ".join(text.split())


def _numbers(text: str) -> set[Decimal]:
    return {
        Decimal(number.replace(",", ""))
        * {
            "k": 1000,
            "thousand": 1000,
            "m": 1000000,
            "million": 1000000,
            "bn": 1000000000,
            "billion": 1000000000,
        }.get(suffix.lower(), 1)
        for number, suffix in re.findall(
            r"(?<![\w.,])(\d+(?:,\d{3})*(?:\.\d+)?)(?:\s*(thousand|million|billion|bn|k|m))?(?!\w|[.,]\d)",
            text,
            re.I,
        )
    }


def dates(text: str) -> set[date]:
    found: set[date] = set()
    for value in re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text):
        try:
            found.add(date.fromisoformat(value))
        except ValueError:
            continue
    months = {
        name.lower(): index
        for index, name in enumerate(
            "January February March April May June July August September October November December".split(),
            1,
        )
    }
    for day, month, year in re.findall(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})\b", text
    ):
        if month.lower() in months:
            try:
                found.add(date(int(year), months[month.lower()], int(day)))
            except ValueError:
                continue
    return found


def _qualified_number(text: str, value: Decimal) -> bool:
    for match in re.finditer(
        r"\b(?:around|approximately|about|a little over|over|up to|at least)\s*(?:GBP|USD|EUR|[£$€])?\s*(\d+(?:,\d{3})*(?:\.\d+)?(?:\s*(?:thousand|million|billion|bn|k|m))?)(?!\w|[.,]\d)",
        text,
        re.I,
    ):
        if value in _numbers(match.group(1)):
            return True
    return False


def check_provenance(
    facts: CaseFacts, bundle: EvidenceBundle
) -> Result[None, PipelineError]:
    blocks = {block.id: block for block in bundle.blocks}
    if facts.effective_date and not any(
        facts.effective_date in dates(block.text)
        for block in bundle.blocks
        if block.role == "evidence"
    ):
        return failure(
            "unsupported_date",
            "The report effective date is not present in the evidence.",
        )
    items: list[FactModel] = [
        *facts.observations,
        *facts.actions,
        *facts.receipts,
        *facts.commitments,
        *facts.narratives,
        *facts.fees,
        *facts.planned_accounts,
    ]
    refs = [
        *facts.scope_refs,
        *(ref for item in items for ref in getattr(item, "refs", [])),
        *(ref for item in [*facts.review_items, *facts.conflicts] for ref in item.refs),
    ]
    for ref in refs:
        block = blocks.get(ref.evidence_id)
        if (
            not block
            or block.role != "evidence"
            or not ref.excerpt.strip()
            or normal(ref.excerpt) not in normal(block.text)
        ):
            return Err(
                ExtractionError(
                    "unsupported_reference",
                    "A fact has an invalid evidence reference or excerpt.",
                    "reconcile",
                    details={
                        "evidence_id": ref.evidence_id,
                        "excerpt": ref.excerpt,
                        "source_text": block.text
                        if block and block.role == "evidence"
                        else None,
                    },
                )
            )
    for item in items:
        item_refs: list[SourceRef] = getattr(item, "refs", [])
        if not item_refs:
            return failure(
                "missing_provenance", "A material fact has no supporting evidence."
            )
        amounts = [getattr(item, "amount", None)]
        if isinstance(item, Account):
            amounts.append(item.valuation)
        cited_text = " ".join(ref.excerpt for ref in item_refs)
        supported = _numbers(cited_text)
        source_names = {blocks[ref.evidence_id].source for ref in item_refs}
        source_dates = set().union(
            *(
                dates(block.text)
                for block in bundle.blocks
                if block.source in source_names and block.role == "evidence"
            )
        )
        for amount in amounts:
            if amount is not None and amount.value not in supported:
                return failure(
                    "unsupported_amount",
                    "An amount is not supported by its cited evidence.",
                )
            if (
                amount is not None
                and amount.effective_date
                and amount.effective_date not in source_dates
            ):
                return failure(
                    "unsupported_date",
                    "An amount's effective date is not supported by its source.",
                )
            if (
                amount is not None
                and amount.precision == "exact"
                and _qualified_number(cited_text, amount.value)
            ):
                return failure(
                    "unsupported_precision",
                    "Qualified evidence cannot support an exact amount.",
                )
        if isinstance(item, Fee) and item.rate_percent is not None:
            rates = {
                Decimal(value)
                for value in re.findall(
                    r"(?<![\d.])(\d+(?:\.\d+)?)\s*(?:%|per cent|percent)",
                    cited_text,
                    re.I,
                )
            }
            if item.rate_percent not in rates:
                return failure(
                    "unsupported_amount",
                    "A fee rate is not supported by its cited evidence.",
                )
    return Ok(None)
