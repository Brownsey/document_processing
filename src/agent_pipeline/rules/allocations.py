"""Verify allocation routes and outflows against shared source capacities."""

from decimal import Decimal

from agent_pipeline.contracts import Ok, PipelineError, Result
from agent_pipeline.rules.models import (
    Account,
    Action,
    CaseFacts,
    Money,
)
from agent_pipeline.rules.review import add_review, failure


def check_source_allocations(
    facts: CaseFacts,
    available: dict[str, Money],
    account_limits: dict[str, Money],
    sale_limits: dict[str, Money],
    receipt_sources: dict[str, str | None],
) -> Result[None, PipelineError]:
    source_money = {"receipt:" + key: money for key, money in available.items()}
    source_money.update(
        {"account:" + key: money for key, money in account_limits.items()}
    )
    source_money.update(
        {
            "sale:" + key: money
            for key, money in sale_limits.items()
            if key in account_limits
        }
    )

    def action_sources(action: Action) -> list[str]:
        sources = ["receipt:" + key for key in set(action.source_funding_ids)]
        if action.source_account_id:
            prefix = (
                "sale:"
                if action.kind == "contribute"
                and action.source_account_id in sale_limits
                else "account:"
            )
            sources.append(prefix + action.source_account_id)
        return sources

    allocations = [
        a
        for a in facts.actions
        if a.status in {"agreed", "conditional"}
        and a.kind in {"contribute", "transfer"}
        and (a.source_funding_ids or a.source_account_id)
    ]
    checked: list[Action] = []
    for action in allocations:
        sources = action_sources(action)
        funding = [source_money[key] for key in sources if key in source_money]
        missing = len(funding) != len(sources)
        if missing or action.amount is None or action.amount.precision != "exact":
            if missing and action.status == "agreed" and action.amount is not None:
                return failure(
                    "unsupported_funding",
                    "An exact unconditional allocation relies on unconfirmed available funds.",
                    blocked=True,
                )
            add_review(
                facts,
                "allocation_confirmation",
                "Confirm exact available funding and allocation amounts before implementation."
                if missing
                else "Confirm allocation amounts before implementation.",
                action.destination_account_ids,
            )
            continue
        if any(amount.currency != action.amount.currency for amount in funding):
            return failure(
                "currency_conflict",
                "An allocation and its funding use incompatible currencies.",
                blocked=True,
            )
        checked.append(action)

    # All routes from an account share one upstream capacity. Disposals create
    # a proceeds pool rather than a second expense; internal receipts draw from
    # that pool, so direct reinvestment and receipt-backed reinvestment compete
    # for the same money. Decimal residual capacities preserve pennies.
    residual: dict[str, dict[str, Decimal]] = {}

    def edge(left: str, right: str, capacity: Decimal) -> None:
        residual.setdefault(left, {})[right] = capacity
        residual.setdefault(right, {}).setdefault(left, Decimal(0))

    for account_id, money in account_limits.items():
        edge("start", "account:" + account_id, money.value)
    for account_id, money in sale_limits.items():
        if account_id in account_limits:
            edge("account:" + account_id, "sale:" + account_id, money.value)
    for receipt_id, money in available.items():
        account_id = receipt_sources.get(receipt_id)
        parent = "start"
        if account_id:
            parent = ("sale:" if account_id in sale_limits else "account:") + account_id
        edge(parent, "receipt:" + receipt_id, money.value)
    required = Decimal(0)
    for index, action in enumerate(checked):
        if action.amount is None:
            continue
        node = "action:" + str(index)
        edge(node, "end", action.amount.value)
        required += action.amount.value
        for source in action_sources(action):
            edge(source, node, action.amount.value)
    return _route_allocations(residual, required)


def _route_allocations(
    residual: dict[str, dict[str, Decimal]], required: Decimal
) -> Result[None, PipelineError]:
    routed = Decimal(0)
    while routed < required:
        parents: dict[str, str] = {"start": ""}
        queue = ["start"]
        for node in queue:
            for neighbor, capacity in residual.get(node, {}).items():
                if capacity > 0 and neighbor not in parents:
                    parents[neighbor] = node
                    queue.append(neighbor)
            if "end" in parents:
                break
        if "end" not in parents:
            return failure(
                "insufficient_funds",
                "The authorised funding sources cannot support all allocations.",
                blocked=True,
            )
        increment = required - routed
        node = "end"
        while node != "start":
            parent = parents[node]
            increment = min(increment, residual[parent][node])
            node = parent
        node = "end"
        while node != "start":
            parent = parents[node]
            residual[parent][node] -= increment
            residual[node][parent] += increment
            node = parent
        routed += increment

    return Ok(None)


def check_outflows(
    facts: CaseFacts, accounts: dict[str, Account], sale_limits: dict[str, Money]
) -> Result[None, PipelineError]:
    for account_id, account in accounts.items():
        outflows = [
            a
            for a in facts.actions
            if a.source_account_id == account_id
            and a.status in {"agreed", "conditional"}
            and a.kind in {"dispose", "transfer", "contribute"}
            and a.amount
        ]
        if not outflows:
            continue
        reinvestments = [
            a for a in outflows if a.kind == "contribute" and account_id in sale_limits
        ]
        if reinvestments:
            sale_limit = sale_limits[account_id]
            if any(
                a.amount and a.amount.currency != sale_limit.currency
                for a in reinvestments
            ):
                return failure(
                    "currency_conflict",
                    "Reinvestment and sale proceeds use incompatible currencies.",
                    blocked=True,
                )
            if (
                sum(
                    (
                        a.amount.value
                        for a in reinvestments
                        if a.amount and not a.source_funding_ids
                    ),
                    Decimal(0),
                )
                > sale_limit.value
            ):
                return failure(
                    "insufficient_funds",
                    "Reinvestments exceed the evidenced sale proceeds.",
                    blocked=True,
                )
            outflows = [a for a in outflows if a not in reinvestments]
        if account.valuation is None or account.valuation.precision != "exact":
            add_review(
                facts,
                "source_balance",
                "Confirm the available source account balance before implementation.",
                [account_id],
            )
            continue
        if any(
            a.amount and a.amount.currency != account.valuation.currency
            for a in outflows
        ):
            return failure(
                "currency_conflict",
                "An outgoing action and its source account use incompatible currencies.",
                blocked=True,
            )
        if (
            sum((a.amount.value for a in outflows if a.amount), Decimal(0))
            > account.valuation.value
        ):
            return failure(
                "insufficient_funds",
                "Outgoing actions exceed the known source account balance.",
                blocked=True,
            )
    return Ok(None)
