"""Hand-calculated scenarios for account, evidence, and funding boundaries."""

import importlib
import importlib.util
from decimal import Decimal

import pytest

from agent_pipeline.contracts import Err, EvidenceBlock, EvidenceBundle, Ok


@pytest.fixture
def domain():
    return (
        importlib.import_module("agent_pipeline.domain")
        if importlib.util.find_spec("agent_pipeline.domain")
        else None
    )


def reference(text, evidence_id="meeting"):
    return {"evidence_id": evidence_id, "excerpt": text}


def bundle(text="", *, duplicate=False, value=40000):
    account = {
        "account_id": "GIA-1",
        "platform": "Platform",
        "type": "GIA",
        "owner": "Joint",
        "status": "open",
        "value": value,
        "currency": "GBP",
        "valuation_date": "2026-03-01",
    }
    db = {"holders": {"one": {"name": "Alex Jones", "accounts": [account]}}}
    if duplicate:
        db["holders"]["two"] = {"name": "Sam Jones", "accounts": [dict(account)]}
    import json

    return EvidenceBundle(
        [
            EvidenceBlock("db", "renamed.json", "$", json.dumps(db), "dbhash"),
            EvidenceBlock("meeting", "conversation.docx", "p1", text, "meetinghash"),
        ],
        [],
        [db],
    )


def facts(domain, **kwargs):
    return domain.CaseFacts(**kwargs)


def test_schema_for_structured_outputs_requires_all_fields_and_forbids_extras(domain):
    assert domain is not None, "Domain reconciliation is not implemented"
    schema = domain.extraction_schema()

    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            assert "default" not in node
            for child in node.values():
                check(child)
        elif isinstance(node, list):
            for child in node:
                check(child)

    check(schema)


def test_joint_identity_from_database_combines_holders_without_doubling_value(domain):
    assert domain is not None, "Domain reconciliation is not implemented"
    result = domain.reconcile(facts(domain), bundle(duplicate=True))
    assert isinstance(result, Ok)
    assert len(result.value.accounts) == 1
    account = result.value.accounts[0]
    assert account.owners == ["Alex Jones", "Sam Jones"]
    assert account.valuation.value == Decimal("40000")


def test_newer_qualified_observation_wins_by_date_not_largest_amount(domain):
    assert domain is not None, "Domain reconciliation is not implemented"
    text = "On 14 May 2026 the GIA was a little over 35,000."
    observations = [
        {
            "account_id": "GIA-1",
            "amount": {
                "value": "35000",
                "currency": "GBP",
                "effective_date": "2026-05-14",
                "precision": "lower_bound",
            },
            "refs": [reference(text)],
        }
    ]
    result = domain.reconcile(facts(domain, observations=observations), bundle(text))
    assert isinstance(result, Ok)
    assert result.value.accounts[0].valuation.value == Decimal("35000")
    assert result.value.accounts[0].valuation.precision == "lower_bound"


def test_undated_observation_cannot_silently_replace_dated_value(domain):
    assert domain is not None, "Domain reconciliation is not implemented"
    text = "GIA balance around 90,000."
    result = domain.reconcile(
        facts(
            domain,
            observations=[
                {
                    "account_id": "GIA-1",
                    "amount": {"value": "90000", "precision": "approximate"},
                    "refs": [reference(text)],
                }
            ],
        ),
        bundle(text),
    )
    assert isinstance(result, Ok)
    assert result.value.accounts[0].valuation.value == Decimal("40000")
    assert any("undated" in item.message.lower() for item in result.value.review_items)


def test_equal_date_conflicting_values_block_selected_account(domain):
    assert domain is not None, "Domain reconciliation is not implemented"
    text = "At 1 March 2026 GIA value 41,000."
    result = domain.reconcile(
        facts(
            domain,
            requested_account_ids=["GIA-1"],
            scope_refs=[reference(text)],
            observations=[
                {
                    "account_id": "GIA-1",
                    "amount": {"value": "41000", "effective_date": "2026-03-01"},
                    "refs": [reference(text)],
                }
            ],
        ),
        bundle(text),
    )
    assert isinstance(result, Err)
    assert result.error.code == "valuation_conflict"


@pytest.mark.parametrize(
    "ref", [reference("invented sentence"), reference("40,000", "missing")]
)
def test_fabricated_excerpt_or_unknown_evidence_id_is_rejected(domain, ref):
    assert domain is not None, "Domain reconciliation is not implemented"
    result = domain.reconcile(
        facts(
            domain,
            narratives=[{"category": "objective", "text": "Growth", "refs": [ref]}],
        ),
        bundle("40,000"),
    )
    assert isinstance(result, Err)
    assert result.error.code == "unsupported_reference"


def test_amount_must_appear_in_its_evidence_without_inflated_suffix(domain):
    assert domain is not None, "Domain reconciliation is not implemented"
    text = "Received 85,000."
    result = domain.reconcile(
        facts(
            domain,
            receipts=[
                {
                    "receipt_id": "sale",
                    "description": "Sale",
                    "amount": {"value": "850000"},
                    "status": "received",
                    "refs": [reference(text)],
                }
            ],
        ),
        bundle(text),
    )
    assert isinstance(result, Err)
    assert result.error.code == "unsupported_amount"


def test_completion_funds_reserve_loan_once_exclude_earnout_and_approximate_receipts(
    domain,
):
    assert domain is not None, "Domain reconciliation is not implemented"
    text = "Received 850,000; reserve 200,000 loan. Earnout up to 400,000. Around 10,000 gift."
    refs = [reference(text)]
    result = domain.reconcile(
        facts(
            domain,
            receipts=[
                {
                    "receipt_id": "sale",
                    "description": "Completion",
                    "amount": {"value": "850000"},
                    "status": "received",
                    "refs": refs,
                },
                {
                    "receipt_id": "earnout",
                    "description": "Earnout",
                    "amount": {"value": "400000", "precision": "upper_bound"},
                    "status": "contingent",
                    "refs": refs,
                },
                {
                    "receipt_id": "gift",
                    "description": "Gift",
                    "amount": {"value": "10000", "precision": "approximate"},
                    "status": "received",
                    "refs": refs,
                },
            ],
            commitments=[
                {
                    "commitment_id": "loan",
                    "receipt_id": "sale",
                    "description": "Loan",
                    "amount": {"value": "200000"},
                    "refs": refs,
                }
            ],
        ),
        bundle(text),
    )
    assert isinstance(result, Ok)
    assert [
        (x.receipt_id, x.available.value) for x in result.value.funding_balances
    ] == [("sale", Decimal("650000"))]


def test_paid_commitment_already_reflected_is_not_subtracted_again(domain):
    assert domain is not None, "Domain reconciliation is not implemented"
    text = "650,000 remaining after the 200,000 loan was paid."
    refs = [reference(text)]
    result = domain.reconcile(
        facts(
            domain,
            receipts=[
                {
                    "receipt_id": "sale",
                    "description": "Remaining proceeds",
                    "amount": {"value": "650000"},
                    "status": "received",
                    "refs": refs,
                }
            ],
            commitments=[
                {
                    "commitment_id": "loan",
                    "receipt_id": "sale",
                    "description": "Loan",
                    "amount": {"value": "200000"},
                    "status": "paid",
                    "already_reflected": True,
                    "refs": refs,
                }
            ],
        ),
        bundle(text),
    )
    assert isinstance(result, Ok)
    assert result.value.funding_balances[0].available.value == Decimal("650000")


def test_scope_unknown_identity_is_blocked_not_guessed(domain):
    assert domain is not None, "Domain reconciliation is not implemented"
    text = "Cover the investment account."
    result = domain.reconcile(
        facts(
            domain, requested_account_ids=["GIA-OTHER"], scope_refs=[reference(text)]
        ),
        bundle(text),
    )
    assert isinstance(result, Err)
    assert result.error.code == "unknown_account"


def test_partial_disposal_does_not_release_entire_balance(domain):
    assert domain is not None, "Domain reconciliation is not implemented"
    text = "Sell a portion of the GIA to rebalance it."
    result = domain.reconcile(
        facts(
            domain,
            actions=[
                {
                    "action_id": "sell",
                    "kind": "dispose",
                    "source_account_id": "GIA-1",
                    "extent": "partial",
                    "rationale": "Rebalance",
                    "refs": [reference(text)],
                }
            ],
        ),
        bundle(text),
    )
    assert isinstance(result, Ok)
    assert result.value.actions[0].amount is None
    assert result.value.funding_balances == []
    assert any("partial" in x.message.lower() for x in result.value.review_items)


def test_relative_tax_period_uses_source_date_not_wall_clock(domain):
    assert domain is not None, "Domain reconciliation is not implemented"
    text = "At our 14 May 2026 meeting use this tax year."
    result = domain.reconcile(
        facts(
            domain,
            effective_date="2026-05-14",
            narratives=[
                {
                    "category": "timing",
                    "text": "Use this tax year",
                    "refs": [reference(text)],
                }
            ],
        ),
        bundle(text),
    )
    assert isinstance(result, Ok)
    assert result.value.tax_year == "2026/27"


def test_missing_relative_date_anchor_is_flagged(domain):
    assert domain is not None, "Domain reconciliation is not implemented"
    text = "Use this tax year."
    result = domain.reconcile(
        facts(
            domain,
            narratives=[
                {
                    "category": "timing",
                    "text": "Use this tax year",
                    "refs": [reference(text)],
                }
            ],
        ),
        bundle(text),
    )
    assert isinstance(result, Ok)
    assert result.value.tax_year is None
    assert any(x.code == "ambiguous_date" for x in result.value.review_items)


def test_qualified_evidence_cannot_be_promoted_to_exact_value(domain):
    text = "On 14 May 2026 the GIA was around 35,000."
    result = domain.reconcile(
        facts(
            domain,
            observations=[
                {
                    "account_id": "GIA-1",
                    "amount": {
                        "value": "35000",
                        "effective_date": "2026-05-14",
                        "precision": "exact",
                    },
                    "refs": [reference(text)],
                }
            ],
        ),
        bundle(text),
    )
    assert isinstance(result, Err)
    assert result.error.code == "unsupported_precision"


def test_invented_valuation_date_cannot_win_recency(domain):
    text = "On 14 May 2026 the GIA was 35,000."
    result = domain.reconcile(
        facts(
            domain,
            observations=[
                {
                    "account_id": "GIA-1",
                    "amount": {"value": "35000", "effective_date": "2030-01-01"},
                    "refs": [reference(text)],
                }
            ],
        ),
        bundle(text),
    )
    assert isinstance(result, Err)
    assert result.error.code == "unsupported_date"


def test_citing_non_evidence_cannot_support_client_facts(domain):
    source = bundle("Invest 785,000.")
    source.blocks[1] = EvidenceBlock(
        "meeting", "general.docx", "p1", "Invest 785,000.", "hash", "excluded"
    )
    result = domain.reconcile(
        facts(
            domain,
            receipts=[
                {
                    "receipt_id": "x",
                    "description": "Client cash",
                    "amount": {"value": "785000"},
                    "status": "received",
                    "refs": [reference("Invest 785,000.")],
                }
            ],
        ),
        source,
    )
    assert isinstance(result, Err)
    assert result.error.code == "unsupported_reference"


def test_fee_rate_cannot_be_borrowed_from_unrelated_numeric_value(domain):
    text = "Risk profile 4; charges unconfirmed."
    result = domain.reconcile(
        facts(
            domain,
            fees=[{"kind": "initial", "rate_percent": "4", "refs": [reference(text)]}],
        ),
        bundle(text),
    )
    assert isinstance(result, Err)
    assert result.error.code == "unsupported_amount"


def test_zero_initial_rate_remains_supported(domain):
    text = "Initial charge 0%."
    result = domain.reconcile(
        facts(
            domain,
            fees=[
                {
                    "kind": "initial",
                    "rate_percent": "0",
                    "confirmed": True,
                    "refs": [reference(text)],
                }
            ],
        ),
        bundle(text),
    )
    assert isinstance(result, Ok)
    assert result.value.fees[0].rate_percent == Decimal("0")
    assert {item.code for item in result.value.review_items} >= {
        "platform_fees",
        "ongoing_advice_fees",
    }


def test_simultaneous_retain_and_full_disposal_block(domain):
    text = "Retain GIA. Dispose of the GIA in full."
    result = domain.reconcile(
        facts(
            domain,
            actions=[
                {
                    "action_id": "keep",
                    "kind": "retain",
                    "source_account_id": "GIA-1",
                    "refs": [reference(text)],
                },
                {
                    "action_id": "sell",
                    "kind": "dispose",
                    "extent": "full",
                    "source_account_id": "GIA-1",
                    "refs": [reference(text)],
                },
            ],
        ),
        bundle(text),
    )
    assert isinstance(result, Err)
    assert result.error.code == "contradictory_actions"


def test_multiple_contributions_cannot_spend_same_received_funds_twice(domain):
    text = "Received 40,000. Invest 30,000 then another 30,000."
    refs = [reference(text)]
    result = domain.reconcile(
        facts(
            domain,
            receipts=[
                {
                    "receipt_id": "cash",
                    "description": "Cash",
                    "status": "received",
                    "amount": {"value": "40000"},
                    "refs": refs,
                }
            ],
            actions=[
                {
                    "action_id": "one",
                    "kind": "contribute",
                    "destination_account_ids": ["GIA-1"],
                    "source_funding_ids": ["cash"],
                    "amount": {"value": "30000"},
                    "refs": refs,
                },
                {
                    "action_id": "two",
                    "kind": "contribute",
                    "destination_account_ids": ["GIA-1"],
                    "source_funding_ids": ["cash"],
                    "amount": {"value": "30000"},
                    "refs": refs,
                },
            ],
        ),
        bundle(text),
    )
    assert isinstance(result, Err)
    assert result.error.code == "insufficient_funds"


def test_mixed_currency_commitment_blocks_exact_calculation(domain):
    text = "Received GBP 40,000; owe USD 5,000."
    refs = [reference(text)]
    result = domain.reconcile(
        facts(
            domain,
            receipts=[
                {
                    "receipt_id": "cash",
                    "description": "Cash",
                    "status": "received",
                    "amount": {"value": "40000", "currency": "GBP"},
                    "refs": refs,
                }
            ],
            commitments=[
                {
                    "commitment_id": "debt",
                    "receipt_id": "cash",
                    "description": "Debt",
                    "amount": {"value": "5000", "currency": "USD"},
                    "refs": refs,
                }
            ],
        ),
        bundle(text),
    )
    assert isinstance(result, Err)
    assert result.error.code == "currency_conflict"


def test_reconciliation_does_not_mutate_original_facts(domain):
    original = facts(domain)
    result = domain.reconcile(original, bundle(duplicate=True))
    assert isinstance(result, Ok)
    assert original.accounts == []
    assert original.review_items == []


@pytest.mark.parametrize(
    "source, value",
    [
        ("Received £45k.", "45000"),
        ("Received £1.2m.", "1200000"),
        ("Received GBP 2 million.", "2000000"),
    ],
)
def test_supported_numeric_suffixes_normalize_without_amount_ceiling(
    domain, source, value
):
    result = domain.reconcile(
        facts(
            domain,
            receipts=[
                {
                    "receipt_id": "cash",
                    "description": "Cash",
                    "amount": {"value": value},
                    "status": "received",
                    "refs": [reference(source)],
                }
            ],
        ),
        bundle(source),
    )
    assert isinstance(result, Ok)
    assert result.value.funding_balances[0].available.value == Decimal(value)


def test_numeric_suffix_qualifier_is_not_lost(domain):
    text = "Received around £1.2m."
    result = domain.reconcile(
        facts(
            domain,
            receipts=[
                {
                    "receipt_id": "cash",
                    "description": "Cash",
                    "amount": {"value": "1200000"},
                    "status": "received",
                    "refs": [reference(text)],
                }
            ],
        ),
        bundle(text),
    )
    assert isinstance(result, Err)
    assert result.error.code == "unsupported_precision"


def test_disposal_of_unscoped_account_is_blocked(domain):
    text = "Cover a planned account only. Sell GIA."
    result = domain.reconcile(
        facts(
            domain,
            requested_account_ids=["new"],
            scope_refs=[reference(text)],
            planned_accounts=[{"account_id": "new", "refs": [reference(text)]}],
            actions=[
                {
                    "action_id": "sale",
                    "kind": "dispose",
                    "source_account_id": "GIA-1",
                    "extent": "full",
                    "refs": [reference(text)],
                }
            ],
        ),
        bundle(text),
    )
    assert isinstance(result, Err)
    assert result.error.code == "out_of_scope_action"


def test_overlapping_pooled_allocations_count_each_receipt_once(domain):
    text = "Received 40,000 and 30,000. Allocate 60,000 then 20,000."
    refs = [reference(text)]
    result = domain.reconcile(
        facts(
            domain,
            receipts=[
                {
                    "receipt_id": "one",
                    "description": "First receipt",
                    "amount": {"value": "40000"},
                    "status": "received",
                    "refs": refs,
                },
                {
                    "receipt_id": "two",
                    "description": "Second receipt",
                    "amount": {"value": "30000"},
                    "status": "received",
                    "refs": refs,
                },
            ],
            actions=[
                {
                    "action_id": "first",
                    "kind": "contribute",
                    "destination_account_ids": ["GIA-1"],
                    "source_funding_ids": ["one", "two"],
                    "amount": {"value": "60000"},
                    "refs": refs,
                },
                {
                    "action_id": "second",
                    "kind": "contribute",
                    "destination_account_ids": ["GIA-1"],
                    "source_funding_ids": ["one"],
                    "amount": {"value": "20000"},
                    "refs": refs,
                },
            ],
        ),
        bundle(text),
    )
    assert isinstance(result, Err)
    assert result.error.code == "insufficient_funds"


def test_database_decimal_values_do_not_require_float_conversion(domain):
    source = bundle(value=31500.27)
    source.databases[0]["holders"]["one"]["accounts"][0]["value"] = Decimal("31500.27")
    result = domain.reconcile(facts(domain), source)
    assert isinstance(result, Ok)
    assert result.value.accounts[0].valuation.value == Decimal("31500.27")


def test_disposal_then_reinvestment_is_one_outflow_not_two(domain):
    text = "Sell 30,000 from GIA. Reinvest those 30,000 proceeds."
    refs = [reference(text)]
    result = domain.reconcile(
        facts(
            domain,
            actions=[
                {
                    "action_id": "sell",
                    "kind": "dispose",
                    "source_account_id": "GIA-1",
                    "extent": "partial",
                    "amount": {"value": "30000"},
                    "refs": refs,
                },
                {
                    "action_id": "invest",
                    "kind": "contribute",
                    "source_account_id": "GIA-1",
                    "amount": {"value": "30000"},
                    "refs": refs,
                },
            ],
        ),
        bundle(text),
    )
    assert isinstance(result, Ok)
    assert len(result.value.actions) == 2


def test_partial_sale_does_not_release_larger_account_value_as_a_receipt(domain):
    text = "Sell 5,000 from GIA worth 40,000; invest 30,000."
    refs = [reference(text)]
    result = domain.reconcile(
        facts(
            domain,
            receipts=[
                {
                    "receipt_id": "sale",
                    "description": "Sale",
                    "source_account_id": "GIA-1",
                    "status": "received",
                    "amount": {"value": "40000"},
                    "refs": refs,
                }
            ],
            actions=[
                {
                    "action_id": "sell",
                    "kind": "dispose",
                    "source_account_id": "GIA-1",
                    "extent": "partial",
                    "amount": {"value": "5000"},
                    "refs": refs,
                },
                {
                    "action_id": "invest",
                    "kind": "contribute",
                    "source_funding_ids": ["sale"],
                    "amount": {"value": "30000"},
                    "refs": refs,
                },
            ],
        ),
        bundle(text),
    )
    assert isinstance(result, Err)
    assert result.error.code == "insufficient_funds"


@pytest.mark.parametrize(
    "direct_amount, receipt_amount, blocked", [(100, 100, True), (40, 60, False)]
)
def test_direct_and_receipt_routes_share_one_cash_balance(
    domain, direct_amount, receipt_amount, blocked
):
    import json

    text = f"Cash holds 100. Allocate {receipt_amount} through the cash receipt and transfer {direct_amount} directly."
    refs = [reference(text)]
    source = bundle(text, value=100)
    source.databases[0]["holders"]["one"]["accounts"][0]["type"] = "Cash Account"
    source.blocks[0] = EvidenceBlock(
        "db", "renamed.json", "$", json.dumps(source.databases[0]), "dbhash"
    )
    result = domain.reconcile(
        facts(
            domain,
            receipts=[
                {
                    "receipt_id": "cash",
                    "description": "Existing cash",
                    "source_account_id": "GIA-1",
                    "status": "received",
                    "amount": {"value": "100"},
                    "refs": refs,
                }
            ],
            actions=[
                {
                    "action_id": "receipt-route",
                    "kind": "contribute",
                    "source_funding_ids": ["cash"],
                    "amount": {"value": str(receipt_amount)},
                    "refs": refs,
                },
                {
                    "action_id": "direct-route",
                    "kind": "transfer",
                    "source_account_id": "GIA-1",
                    "amount": {"value": str(direct_amount)},
                    "refs": refs,
                },
            ],
        ),
        source,
    )
    assert isinstance(result, Err if blocked else Ok)
    if isinstance(result, Err):
        assert result.error.code == "insufficient_funds"


@pytest.mark.parametrize("allocation, blocked", [(20000, True), (15000, False)])
def test_direct_and_receipt_reinvestment_share_actual_sale_proceeds(
    domain, allocation, blocked
):
    text = f"Sell 30,000 from GIA. Reinvest {allocation} from the sale receipt and {allocation} directly from those same proceeds."
    refs = [reference(text)]
    result = domain.reconcile(
        facts(
            domain,
            receipts=[
                {
                    "receipt_id": "sale",
                    "description": "Received sale proceeds",
                    "source_account_id": "GIA-1",
                    "status": "received",
                    "amount": {"value": "30000"},
                    "refs": refs,
                }
            ],
            actions=[
                {
                    "action_id": "sell",
                    "kind": "dispose",
                    "source_account_id": "GIA-1",
                    "extent": "partial",
                    "amount": {"value": "30000"},
                    "refs": refs,
                },
                {
                    "action_id": "receipt-route",
                    "kind": "contribute",
                    "source_funding_ids": ["sale"],
                    "amount": {"value": str(allocation)},
                    "refs": refs,
                },
                {
                    "action_id": "direct-route",
                    "kind": "contribute",
                    "source_account_id": "GIA-1",
                    "amount": {"value": str(allocation)},
                    "refs": refs,
                },
            ],
        ),
        bundle(text),
    )
    assert isinstance(result, Err if blocked else Ok)
    if isinstance(result, Err):
        assert result.error.code == "insufficient_funds"


@pytest.mark.parametrize(
    "commitment_amount", [None, {"value": "20", "precision": "approximate"}]
)
@pytest.mark.parametrize("action_status", ["agreed", "conditional"])
def test_unconfirmed_commitments_withhold_exact_direct_account_capacity(
    domain, commitment_amount, action_status
):
    import json

    text = "Cash holds 100. Reserve around 20 for an obligation whose amount needs confirmation. Invest 80 subject to confirming the obligation."
    refs = [reference(text)]
    source = bundle(text, value=100)
    source.databases[0]["holders"]["one"]["accounts"][0]["type"] = "Cash Account"
    source.blocks[0] = EvidenceBlock(
        "db", "renamed.json", "$", json.dumps(source.databases[0]), "dbhash"
    )
    result = domain.reconcile(
        facts(
            domain,
            receipts=[
                {
                    "receipt_id": "cash",
                    "description": "Existing cash",
                    "source_account_id": "GIA-1",
                    "status": "received",
                    "amount": {"value": "100"},
                    "refs": refs,
                }
            ],
            commitments=[
                {
                    "commitment_id": "cost",
                    "receipt_id": "cash",
                    "description": "Outstanding obligation",
                    "amount": commitment_amount,
                    "refs": refs,
                }
            ],
            actions=[
                {
                    "action_id": "invest",
                    "kind": "contribute",
                    "source_account_id": "GIA-1",
                    "status": action_status,
                    "amount": {"value": "80"},
                    "conditions": ["Confirm obligation"],
                    "refs": refs,
                }
            ],
        ),
        source,
    )
    if action_status == "agreed":
        assert isinstance(result, Err)
        assert result.error.code == "unsupported_funding"
    else:
        assert isinstance(result, Ok)
        assert any(
            item.code == "unknown_commitment" for item in result.value.review_items
        )


def test_pending_receipt_cannot_hide_currency_mismatch_in_shared_commitment(domain):
    import json

    text = "Cash holds GBP 100. Pending cash movement GBP 100. Reserve USD 20 and invest GBP 80."
    refs = [reference(text)]
    source = bundle(text, value=100)
    source.databases[0]["holders"]["one"]["accounts"][0]["type"] = "Cash Account"
    source.blocks[0] = EvidenceBlock(
        "db", "renamed.json", "$", json.dumps(source.databases[0]), "dbhash"
    )
    result = domain.reconcile(
        facts(
            domain,
            receipts=[
                {
                    "receipt_id": "cash",
                    "description": "Existing cash",
                    "source_account_id": "GIA-1",
                    "status": "pending",
                    "amount": {"value": "100"},
                    "refs": refs,
                }
            ],
            commitments=[
                {
                    "commitment_id": "cost",
                    "receipt_id": "cash",
                    "description": "Outstanding obligation",
                    "amount": {"value": "20", "currency": "USD"},
                    "refs": refs,
                }
            ],
            actions=[
                {
                    "action_id": "invest",
                    "kind": "contribute",
                    "source_account_id": "GIA-1",
                    "amount": {"value": "80"},
                    "refs": refs,
                }
            ],
        ),
        source,
    )
    assert isinstance(result, Err)
    assert result.error.code == "currency_conflict"
