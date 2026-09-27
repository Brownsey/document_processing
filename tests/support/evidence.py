"""Synthetic account and meeting evidence shared by rule tests."""

import json

from agent_pipeline.contracts import EvidenceBlock, EvidenceBundle


def evidence(text):
    database = {
        "holders": {
            "client": {
                "name": "Alex",
                "accounts": [
                    {
                        "account_id": "GIA",
                        "platform": "Platform",
                        "type": "General Investment Account",
                        "owner": "Alex",
                        "status": "open",
                        "value": 30000,
                        "currency": "GBP",
                        "valuation_date": "2026-04-30",
                    },
                    {
                        "account_id": "ISA",
                        "platform": "Platform",
                        "type": "ISA",
                        "owner": "Alex",
                        "status": "open",
                        "value": 10000,
                        "currency": "GBP",
                        "valuation_date": "2026-04-30",
                    },
                ],
            }
        }
    }
    return EvidenceBundle(
        [
            EvidenceBlock("db", "accounts.json", "$", json.dumps(database), "dbhash"),
            EvidenceBlock("meeting", "notes.docx", "paragraph 1", text, "meetinghash"),
        ],
        [],
        [database],
    )


def ref(text):
    return [{"evidence_id": "meeting", "excerpt": text}]
