"""Consistent reconciliation failures and deduplicated adviser review items."""

from agent_pipeline.contracts import Err, ExtractionError, PipelineError, ReportBlocked
from agent_pipeline.rules.models import CaseFacts, ReviewItem


def failure(code: str, message: str, *, blocked: bool = False) -> Err[PipelineError]:
    error_type = ReportBlocked if blocked else ExtractionError
    return Err(error_type(code, message, stage="reconcile"))


def add_review(
    facts: CaseFacts, code: str, message: str, account_ids: list[str] | None = None
) -> None:
    item = ReviewItem(
        code=code,
        message=message,
        account_ids=account_ids or [],
        category="discrepancy" if code == "valuation_conflict" else "confirmation",
    )
    if item not in facts.review_items:
        facts.review_items.append(item)
