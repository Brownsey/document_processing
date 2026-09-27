from agent_pipeline.rendering import render_slot


def test_review_note_keeps_its_account_in_the_same_sentence():
    text = render_slot(
        "actions",
        {
            "accounts": [
                {"account_id": "C", "platform": "Cedar", "account_type": "Cash Account"}
            ],
            "review_items": [
                {"message": "Confirm the outstanding balance.", "account_ids": ["C"]}
            ],
        },
        {},
    )
    assert "Confirm the outstanding balance (Cedar Cash Account (C))." in text


def test_planned_joint_account_has_clear_joint_label_when_type_ends_with_joint():
    text = render_slot(
        "actions",
        {
            "planned_accounts": [
                {
                    "account_id": "NEW",
                    "account_type": "Investment account (joint)",
                    "owners": ["Avery", "Casey"],
                    "status": "planned",
                }
            ],
            "actions": [{"kind": "open", "destination_account_ids": ["NEW"]}],
        },
        {},
    )
    assert "Open a joint investment account for Avery and Casey." in text
    assert "NEW" not in text


def test_shared_wrapper_destinations_keep_ids_and_one_total_without_implying_equal_split():
    facts = {
        "accounts": [
            {"account_id": "A", "platform": "Cedar", "account_type": "ISA"},
            {"account_id": "B", "platform": "Cedar", "account_type": "ISA"},
        ],
        "actions": [
            {
                "kind": "contribute",
                "destination_account_ids": ["A", "B"],
                "amount": {"value": "13000"},
                "allocation_rule": "Sizing to be confirmed",
            }
        ],
    }
    text = render_slot("actions", facts, {})
    assert "to Cedar ISAs (A and B)." in text
    assert text.count("£13,000") == 1
    assert "equal" not in text and "each" not in text
    facts["accounts"][1]["account_type"] = "SIPP"
    mixed = render_slot("actions", facts, {})
    assert "Cedar ISA (A) and Cedar SIPP (B)" in mixed
    assert "ISAs" not in mixed


def test_missing_contribution_size_is_not_presented_as_an_agreed_amount():
    text = render_slot("actions", {"actions": [{"kind": "contribute"}]}, {})
    assert "amount to be confirmed" in text
    assert "agreed amount" not in text


def test_rebalance_names_the_account_without_inventing_a_transfer_or_amount():
    facts = {
        "accounts": [{"account_id": "GIA", "platform": "Alpha", "account_type": "GIA"}],
        "actions": [
            {
                "kind": "rebalance",
                "source_account_id": "GIA",
                "timing": "After the agreed partial sale",
            }
        ],
    }
    text = render_slot("actions", facts, {})
    assert "Rebalance Alpha GIA (GIA)." in text
    assert "agreed amount" not in text and " from " not in text
    assert "After the agreed partial sale" in text


def test_review_note_names_affected_account_without_expanding_holdings():
    facts = {
        "accounts": [
            {"account_id": "CASH", "platform": "Alpha", "account_type": "Cash"}
        ],
        "requested_account_ids": [],
        "review_items": [
            {
                "code": "missing_balance",
                "message": "Confirm balance and status",
                "account_ids": ["CASH"],
            }
        ],
    }
    assert "Alpha Cash (CASH)" in render_slot("actions", facts, {})
    assert "Alpha Cash" not in render_slot("holdings", facts, {})


def test_zero_initial_fee_has_no_invented_basis_gap_and_cash_transfer_has_no_sale():
    facts = {
        "fees": [{"kind": "initial", "rate_percent": "0", "confirmed": True}],
        "accounts": [{"account_id": "CASH", "account_type": "Cash Account"}],
        "actions": [
            {"kind": "transfer", "source_account_id": "CASH", "status": "agreed"}
        ],
    }
    assert "basis requires confirmation" not in render_slot("fees", facts, {})
    assert "No investments are being sold" in render_slot("actions", facts, {})


def test_actions_leave_explanation_to_narrative_and_normalise_terminal_punctuation():
    text = render_slot(
        "actions",
        {
            "actions": [
                {
                    "kind": "contribute",
                    "rationale": "A reason for the prose.",
                    "timing": "This tax year.",
                    "conditions": ["Confirm capacity."],
                }
            ]
        },
        {},
    )
    assert "A reason for the prose" not in text
    assert "This tax year." in text and "Confirm capacity." in text
    assert ".." not in text


def test_pooled_funding_is_one_allocation_with_qualified_received_funds():
    facts = {
        "accounts": [
            {
                "account_id": "GIA",
                "platform": "Alpha",
                "account_type": "GIA",
                "owners": ["Jo", "Sam"],
            },
            {
                "account_id": "ISA",
                "platform": "Alpha",
                "account_type": "ISA",
                "owners": ["Jo"],
            },
        ],
        "receipts": [
            {
                "receipt_id": "inheritance",
                "description": "Jo's inheritance",
                "status": "received",
                "amount": {"value": "120000", "precision": "approximate"},
            }
        ],
        "actions": [
            {"kind": "dispose", "source_account_id": "GIA", "extent": "full"},
            {
                "kind": "contribute",
                "source_account_id": "GIA",
                "source_funding_ids": ["inheritance"],
                "destination_account_ids": ["ISA"],
                "allocation_rule": "Remaining balance to a new joint account",
                "status": "conditional",
            },
        ],
    }
    text = render_slot("actions", facts, {})
    assert "Combine the proceeds" in text
    assert "Jo's inheritance" in text and "around £120,000" in text
    assert text.count("Alpha ISA (ISA)") == 1
    assert "Remaining balance" in text
    assert "Do not implement" in text
    bounded_facts = {
        **facts,
        "actions": [
            facts["actions"][0],
            {**facts["actions"][1], "amount": {"value": "20000", "precision": "exact"}},
        ],
    }
    bounded = render_slot("actions", bounded_facts, {})
    assert "Allocate £20,000 from the combined funds" in bounded
    assert "Allocate the combined funds" not in bounded


def test_fees_and_tax_have_explicit_human_review_markers_and_platforms():
    facts = {
        "accounts": [
            {
                "account_id": "GIA",
                "platform": "Alpha",
                "account_type": "GIA",
                "owners": ["Jo"],
            }
        ],
        "requested_account_ids": ["GIA"],
        "actions": [
            {"kind": "dispose", "source_account_id": "GIA", "status": "agreed"}
        ],
    }
    fees = render_slot("fees", facts, {})
    assert "Alpha" in fees
    assert "[REVIEW REQUIRED:" in fees
    assert "[REVIEW REQUIRED:" in render_slot("tax", facts, {})


def test_confirmation_preserves_its_subject_without_inventing_a_transfer():
    text = render_slot(
        "actions",
        {
            "accounts": [
                {"account_id": "Z-CASH", "account_type": "Cash", "platform": "Cedar"}
            ],
            "actions": [
                {
                    "kind": "confirm",
                    "source_account_id": "Z-CASH",
                    "rationale": "Confirm the settlement date.",
                    "status": "agreed",
                }
            ],
        },
        {},
    )
    assert "Confirm the settlement date" in text
    assert "Cedar Cash (Z-CASH)" in text
    assert "the agreed amount" not in text
    assert " from " not in text


def test_opening_preserves_known_joint_ownership_in_its_label():
    text = render_slot(
        "actions",
        {
            "planned_accounts": [
                {
                    "account_id": "NEW",
                    "account_type": "Investment account",
                    "owners": ["Rowan", "Taylor"],
                    "status": "planned",
                }
            ],
            "actions": [{"kind": "open", "destination_account_ids": ["NEW"]}],
        },
        {},
    )
    assert "Open a joint investment account for Rowan and Taylor." in text
    assert "NEW" not in text


def test_fee_confirmation_names_platforms_and_preserves_account_coverage():
    facts = {
        "accounts": [
            {"account_id": "A", "platform": "Cedar", "account_type": "ISA"},
            {"account_id": "B", "platform": "Birch", "account_type": "SIPP"},
        ],
        "requested_account_ids": ["A", "B"],
        "fees": [
            {
                "kind": "platform",
                "rate_percent": "0.3",
                "confirmed": True,
                "basis": "assets held",
                "account_ids": ["A"],
            }
        ],
    }
    text = render_slot("fees", facts, {})
    assert "0.3% on assets held for Cedar ISA (A)" in text
    assert "confirm platform charge rate, basis and coverage for Birch SIPP (B)" in text
    assert (
        "confirm ongoing advice charge rate, basis and coverage for all accounts covered by this report"
        in text
    )
