"""Select facts and evidence for model requests and source review."""

import re
from dataclasses import asdict

from agent_pipeline.contracts import EvidenceBundle


def select_facts(selector: str, facts: dict) -> dict:
    recipient_names = list(
        dict.fromkeys(
            owner
            for account in facts.get("accounts", []) + facts.get("planned_accounts", [])
            if account["account_id"] in facts.get("requested_account_ids", [])
            for owner in account.get("owners", [])
        )
    )

    def narrative(item: dict) -> dict:
        return {
            "category": item["category"],
            "text": re.sub(
                r"(?:[£$€]|GBP\s*)\d[\d,.]*|\b\d+(?:\.\d+)?\s*%"
                r"|\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b(?!(?:19|20)\d{2}\b)\d{4,}(?:\.\d+)?\b"
                r"|\b\d+(?:\.\d+)?\s*(?:thousand|million|billion|k)\b",
                "[amount omitted]",
                item["text"],
                flags=re.I,
            ),
        }

    if selector == "background":
        return {
            "recipient_names": recipient_names,
            "narratives": [
                narrative(item)
                for item in facts.get("narratives", [])
                if item.get("category")
                in {"circumstance", "objective", "risk", "timing", "sensitivity"}
                and not re.search(
                    r"\b(?:charges?|costs?|next steps|confirm|disinvest|top.up)\b",
                    item.get("text", ""),
                    re.I,
                )
            ],
        }
    if selector == "rationale":
        account_types = {
            account["account_id"]: account.get("account_type", "")
            for account in facts.get("accounts", []) + facts.get("planned_accounts", [])
        }
        return {
            "recipient_names": recipient_names,
            "narratives": [
                narrative(item)
                for item in facts.get("narratives", [])
                if item.get("category") in {"objective", "rationale"}
            ],
            "actions": [
                {
                    **({"kind": action["kind"]} if "kind" in action else {}),
                    "source_account_type": account_types.get(
                        action.get("source_account_id"), ""
                    ),
                    "destination_account_types": list(
                        dict.fromkeys(
                            account_types[key]
                            for key in action.get("destination_account_ids", [])
                            if account_types.get(key)
                        )
                    ),
                    "rationale": narrative(
                        {"category": "rationale", "text": action["rationale"]}
                    )["text"],
                }
                for action in facts.get("actions", [])
                if action.get("rationale")
                and action.get("status", "agreed") in {"agreed", "conditional"}
            ],
        }
    common = {
        k: facts.get(k, []) for k in ("effective_date", "review_items", "conflicts")
    }
    fields = {
        "introduction": (
            "accounts",
            "requested_account_ids",
            "planned_accounts",
            "scope_refs",
        ),
        "recommendations": (
            "actions",
            "accounts",
            "planned_accounts",
            "requested_account_ids",
            "narratives",
            "receipts",
            "commitments",
            "funding_balances",
        ),
        "tax": ("actions", "accounts"),
        "fees": ("fees", "accounts", "actions"),
    }
    if selector == "all":
        return facts
    selected = common | {k: facts.get(k, []) for k in fields[selector]}
    if selector == "recommendations":
        selected["narratives"] = [
            n
            for n in selected["narratives"]
            if n.get("category") not in {"exclusion", "charges_concern"}
            and not (
                n.get("category") == "sensitivity"
                and re.search(r"\b(?:charges?|costs?)\b", n.get("text", ""), re.I)
            )
        ]
    return selected


def evidence_context(bundle: EvidenceBundle) -> dict:
    return {
        "evidence": [asdict(b) for b in bundle.blocks if b.role == "evidence"],
        "internal_guidance": [asdict(b) for b in bundle.blocks if b.role == "guidance"],
        "source_inventory": bundle.inventory,
    }


def review_facts(value):
    """Keep fact-to-source links; the review receives each complete source separately."""
    if isinstance(value, list):
        return [review_facts(item) for item in value]
    if isinstance(value, dict):
        return {
            key: [
                {"evidence_id": identifier}
                for identifier in dict.fromkeys(ref["evidence_id"] for ref in item)
            ]
            if key in {"refs", "scope_refs"}
            else review_facts(item)
            for key, item in value.items()
        }
    return value
