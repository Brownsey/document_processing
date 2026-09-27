from agent_pipeline.evaluation import _is_action_claim


def test_grouped_scope_aliases_apply_only_to_introduction():
    from agent_pipeline.evaluation import score_report

    expected = {
        "tax": False,
        "initial_rate": "0",
        "accounts": [
            {
                "id": "A-1",
                "aliases": ["A-1"],
                "scope_aliases": ["Alpha ISAs"],
                "owners": ["Alex"],
            },
            {
                "id": "A-2",
                "aliases": ["A-2"],
                "scope_aliases": ["Alpha ISAs"],
                "owners": ["Jo"],
            },
        ],
        "actions": [],
    }

    def checks(intro):
        result = score_report("# Report\n## Introduction\n" + intro, {}, expected)
        return {c["name"]: c["passed"] for c in result["checks"]}

    good = checks("Your Alpha ISAs")
    assert good["introduction scope A-1"] and good["introduction scope A-2"]
    assert not good[
        "A-1 appears once"
    ]  # A group never replaces separate holdings rows.
    for bad in ("Your Beta ISAs", "Your Alpha ISA", "Your Alpha GIAs"):
        assert not checks(bad)["introduction scope A-1"]


def test_confirmation_of_capacity_is_not_a_transaction_directive():
    assert not _is_action_claim(
        "- [REVIEW REQUIRED: Confirm remaining ISA subscription capacity for this tax year before implementing the agreed top-up.]"
    )
    assert _is_action_claim("- [REVIEW REQUIRED: You should sell the ISA.]")
    assert _is_action_claim("Confirm capacity, then sell the ISA")
