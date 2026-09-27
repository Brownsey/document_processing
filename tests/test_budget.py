"""Core budget contracts shared by paid model adapters."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path


def test_core_budget_survives_restart_and_charges_unknown_usage(tmp_path: Path) -> None:
    from agent_pipeline.budget import MoneyBudget

    path = tmp_path / "budget.sqlite"
    first = MoneyBudget(path, Decimal("1"))
    reservation = first.reserve(Decimal("0.4"))

    reopened = MoneyBudget(path, Decimal("1"))
    assert reopened.totals() == (Decimal("0"), Decimal("0.4"))
    assert reopened.settle(reservation, None) is False
    assert MoneyBudget(path, Decimal("1")).totals() == (
        Decimal("0.4"),
        Decimal("0"),
    )


def test_concurrent_core_reservations_share_one_cap(tmp_path: Path) -> None:
    from agent_pipeline.budget import MoneyBudget

    path = tmp_path / "budget.sqlite"
    MoneyBudget(path, Decimal("1"))

    def reserve() -> str | None:
        try:
            return MoneyBudget(path, Decimal("1")).reserve(Decimal("0.6"))
        except RuntimeError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        reservations = list(pool.map(lambda _: reserve(), range(2)))

    assert sum(reservation is not None for reservation in reservations) == 1
    assert MoneyBudget(path, Decimal("1")).totals() == (
        Decimal("0"),
        Decimal("0.6"),
    )
