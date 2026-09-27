import json
from copy import deepcopy
from pathlib import Path

from agent_pipeline.reporting.rendering import render_slot


def facts():
    return {
        "accounts": [
            {"account_id": "GIA", "account_type": "General Investment Account"},
            {"account_id": "ISA", "account_type": "ISA"},
        ],
        "planned_accounts": [
            {
                "account_id": "NEW",
                "account_type": "Investment account",
                "status": "planned",
                "owners": ["Alex", "Jo"],
            }
        ],
        "actions": [
            {
                "kind": "contribute",
                "source_account_id": "GIA",
                "source_funding_ids": ["R"],
                "destination_account_ids": ["ISA", "NEW"],
                "allocation_rule": "Fund the ISA and invest the balance jointly",
                "conditions": ["Confirm the allocation"],
                "status": "conditional",
            },
            {"kind": "open", "destination_account_ids": ["NEW"]},
            {
                "kind": "dispose",
                "source_account_id": "GIA",
                "extent": "full",
                "timing": "After approval",
            },
        ],
        "receipts": [
            {
                "receipt_id": "R",
                "description": "Inheritance received by Jo",
                "status": "received",
                "amount": {"value": "91000", "precision": "approximate"},
            }
        ],
        "review_items": [
            {
                "code": "reason",
                "message": "Confirm why a new account is needed.",
                "account_ids": ["GIA", "NEW"],
            }
        ],
    }


def test_shared_template_orders_context_actions_reason_then_queries_without_mutating_facts():
    data = facts()
    original = deepcopy(data)
    config = json.loads(Path("config/template_config.json").read_text(encoding="utf-8"))
    section = next(s for s in config["sections"] if s["id"] == "recommendations")
    text = section["template"]
    for name, spec in section["placeholders"].items():
        value = (
            render_slot(spec["renderer"], data, config)
            if "renderer" in spec
            else "This supports the recorded purpose."
        )
        text = text.replace("<<" + name + ">>", value)
    assert (
        text.index("Funds received")
        < text.index("- Sell")
        < text.index("- Open")
        < text.index("- Combine")
    )
    assert (
        text.index("- Combine")
        < text.index("This supports")
        < text.index("Confirm why")
    )
    assert not any(
        line.startswith("- ") and "Funds received" in line for line in text.splitlines()
    )
    assert "around £91,000" in text and "After approval" in text
    assert "Confirm the allocation" in text and "Do not implement" in text
    assert data == original


def test_new_layout_keeps_implementation_pause_before_any_action_and_legacy_keeps_queries():
    data = facts()
    data["review_items"].append(
        {
            "code": "isa_funding_conflict",
            "message": "Do not implement until the adviser resolves the ISA funding plan.",
            "account_ids": ["GIA", "ISA"],
        }
    )
    plan = render_slot("action_plan", data, {})
    queries = render_slot("adviser_queries", data, {})
    assert plan.index("IMPLEMENTATION PAUSED") < plan.index("- Sell")
    assert "Confirm why" not in plan and "Confirm why" in queries
    assert "IMPLEMENTATION PAUSED" not in queries
    assert "Confirm why" in render_slot("actions", data, {})


def test_funding_and_confirmations_are_prose_and_same_priority_actions_keep_order():
    data = facts()
    data["actions"] += [
        {"kind": "transfer", "destination_account_ids": ["ISA"]},
        {"kind": "confirm", "rationale": "Confirm the instruction"},
    ]
    data["receipts"] += [
        {
            "receipt_id": "P",
            "description": "Earnout",
            "status": "contingent",
            "amount": {"value": "10000"},
        }
    ]
    data["commitments"] = [
        {"description": "Loan", "status": "unpaid", "amount": {"value": "5000"}}
    ]
    data["funding_balances"] = [{"available": {"value": "86000"}}]
    text = (
        render_slot("action_plan", data, {})
        + "\n"
        + render_slot("adviser_queries", data, {})
    )
    for phrase in [
        "Earnout",
        "reserved",
        "Available funding",
        "Confirm the instruction",
    ]:
        assert phrase in text
        assert not any(
            line.startswith("- ") and phrase in line for line in text.splitlines()
        )
    assert text.index("- Combine") < text.index("- Transfer")


def test_no_queries_returns_empty_text_without_inventing_reassurance():
    assert render_slot("adviser_queries", {}, {}) == ""


def test_existing_cash_funding_context_precedes_transfer():
    data = {
        "accounts": [
            {"account_id": "CASH", "account_type": "Cash account"},
            {"account_id": "ISA", "account_type": "ISA"},
        ],
        "actions": [
            {
                "kind": "transfer",
                "source_account_id": "CASH",
                "destination_account_ids": ["ISA"],
            }
        ],
    }
    for renderer in ("actions", "action_plan"):
        text = render_slot(renderer, data, {})
        assert text.index("these transfers use existing cash") < text.index(
            "- Transfer"
        )


def test_pipeline_allows_empty_query_slot_but_keeps_other_shape_checks(tmp_path):
    from support.workflow import CaseProvider, configured_case

    from agent_pipeline.contracts import Ok
    from agent_pipeline.workflow import run_generation

    client, config_path, output, config = configured_case(tmp_path)
    section = config["sections"][-1]
    section["template"] = "<<actions>>\n\n<<queries>>"
    section["placeholders"]["actions"]["renderer"] = "action_plan"
    section["placeholders"]["queries"] = {
        "renderer": "adviser_queries",
        "output_type": "static",
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")
    result = run_generation(
        client_dir=client,
        config_path=config_path,
        output_dir=output,
        provider=CaseProvider(),
    )
    assert isinstance(result, Ok), result
    report = (output / "client.md").read_text(encoding="utf-8")
    assert "Adviser confirmations" not in report
    assert "<<queries>>" not in report
