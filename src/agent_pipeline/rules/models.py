"""Validated financial facts and their evidence references."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SourceRef(FactModel):
    evidence_id: str
    excerpt: str


class Money(FactModel):
    value: Decimal = Field(ge=0)
    currency: str = "GBP"
    effective_date: date | None = None
    precision: Literal[
        "exact", "approximate", "lower_bound", "upper_bound", "unknown"
    ] = "exact"


class Account(FactModel):
    account_id: str = Field(
        description="Existing account ID, or a distinct nonblank local identifier for a planned account without an issued ID. Reuse that identifier in scope and actions."
    )
    platform: str = ""
    account_type: str = ""
    owners: list[str] = Field(
        default_factory=list,
        description="Evidenced owner names, not ownership labels such as 'Joint'. Use [] if names are unknown, preserving explicit joint ownership in account_type.",
    )
    status: Literal["open", "closed", "planned", "unknown"] = "open"
    valuation: Money | None = None
    refs: list[SourceRef] = Field(default_factory=list)


class Observation(FactModel):
    account_id: str
    amount: Money
    basis: Literal["market_value", "cost_basis"] = Field(
        default="market_value",
        description="Use market_value for the current value of holdings, including live meeting valuations; cost_basis only for acquisition costs.",
    )
    refs: list[SourceRef] = Field(default_factory=list)


class Action(FactModel):
    action_id: str
    kind: Literal[
        "contribute",
        "dispose",
        "transfer",
        "open",
        "retain",
        "rebalance",
        "confirm",
        "exclude",
    ]
    source_account_id: str | None = None
    destination_account_ids: list[str] = Field(default_factory=list)
    source_funding_ids: list[str] = Field(default_factory=list)
    amount: Money | None = None
    extent: Literal["full", "partial", "unspecified"] = "unspecified"
    allocation_rule: str | None = None
    rationale: str = Field(
        default="",
        description="Recorded purpose only, without repeating transaction steps, amounts or account identifiers. For confirm actions, state what must be confirmed.",
    )
    timing: str | None = None
    conditions: list[str] = Field(default_factory=list)
    status: Literal["agreed", "conditional", "future", "excluded"] = "agreed"
    refs: list[SourceRef] = Field(default_factory=list)


class Receipt(FactModel):
    receipt_id: str
    description: str
    amount: Money | None = None
    status: Literal["received", "pending", "contingent"]
    source_account_id: str | None = None
    refs: list[SourceRef] = Field(default_factory=list)


class Commitment(FactModel):
    commitment_id: str
    receipt_id: str
    description: str
    amount: Money | None = None
    status: Literal["unpaid", "paid"] = "unpaid"
    already_reflected: bool = Field(
        default=False,
        description="True only if the supplied receipt amount is already net of this commitment; earmarked or due amounts are not deducted.",
    )
    refs: list[SourceRef] = Field(default_factory=list)


class NarrativeFact(FactModel):
    category: Literal[
        "circumstance",
        "objective",
        "risk",
        "timing",
        "sensitivity",
        "exclusion",
        "rationale",
        "charges_concern",
    ]
    text: str
    refs: list[SourceRef] = Field(default_factory=list)


class Fee(FactModel):
    kind: Literal["initial", "platform", "ongoing_advice", "fund", "other"]
    rate_percent: Decimal | None = Field(default=None, ge=0)
    amount: Money | None = None
    basis: str | None = None
    account_ids: list[str] = Field(default_factory=list)
    confirmed: bool = False
    refs: list[SourceRef] = Field(default_factory=list)


class ReviewItem(FactModel):
    code: str
    message: str
    category: Literal["confirmation", "discrepancy"] = Field(
        default="confirmation",
        description="Use discrepancy only for conflicting source facts or instructions; missing figures, rationale or routine checks are confirmation. Material unresolved conflicts must still block generation.",
    )
    account_ids: list[str] = Field(default_factory=list)
    blocking: bool = False
    refs: list[SourceRef] = Field(default_factory=list)


class FundingBalance(FactModel):
    receipt_id: str
    available: Money
    deducted_commitment_ids: list[str] = Field(default_factory=list)


class CaseFacts(FactModel):
    effective_date: date | None = Field(
        default=None,
        description="Date of the meeting or source instruction, when supplied; anchors relative timing. Do not substitute today's date.",
    )
    tax_year: str | None = None
    accounts: list[Account] = Field(default_factory=list)
    requested_account_ids: list[str] = Field(default_factory=list)
    scope_refs: list[SourceRef] = Field(default_factory=list)
    planned_accounts: list[Account] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    receipts: list[Receipt] = Field(default_factory=list)
    commitments: list[Commitment] = Field(default_factory=list)
    narratives: list[NarrativeFact] = Field(default_factory=list)
    fees: list[Fee] = Field(default_factory=list)
    review_items: list[ReviewItem] = Field(default_factory=list)
    conflicts: list[ReviewItem] = Field(default_factory=list)
    funding_balances: list[FundingBalance] = Field(default_factory=list)
