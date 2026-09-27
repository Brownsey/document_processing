"""Dispatch deterministic report sections from reconciled facts."""

from agent_pipeline.pipeline.validation import RISK_WARNING
from agent_pipeline.reporting.accounts import render_holdings, render_scope
from agent_pipeline.reporting.actions import render_actions
from agent_pipeline.reporting.charges import render_fees, render_tax


def render_slot(renderer: str, facts: dict, config: dict) -> str:
    if renderer == "risk_warning":
        return config.get("risk_warning", RISK_WARNING)
    if renderer == "scope":
        return render_scope(facts)
    if renderer == "holdings":
        return render_holdings(facts)
    if renderer == "tax":
        return render_tax(facts)
    if renderer == "fees":
        return render_fees(facts)
    if renderer in {"actions", "action_plan", "adviser_queries"}:
        return render_actions(renderer, facts, config)
    raise ValueError("Unknown renderer")
