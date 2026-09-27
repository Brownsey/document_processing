"""Shared offline client setup and provider responses for workflow tests."""

import json

from agent_pipeline.contracts import Err, ModelReply, Ok, ProviderError
from agent_pipeline.rules.models import CaseFacts


class CaseProvider:
    def __init__(
        self,
        *,
        narrative="Your objective is long-term flexibility.",
        support=True,
        interrupt=False,
    ):
        self.calls = []
        self.records = []
        self.settings = {"model": "fake", "tier": "default"}
        self.narrative = narrative
        self.support = support
        self.interrupt = interrupt

    def complete(self, **request):
        self.calls.append(request)
        if self.interrupt:
            raise KeyboardInterrupt
        task = request["task"]
        data = None
        text = ""
        if task == "extract":
            evidence = next(
                b for b in request["context"]["evidence"] if b["source"] == "notes.txt"
            )
            ref = {"evidence_id": evidence["id"], "excerpt": evidence["text"]}
            data = CaseFacts.model_validate(
                {
                    "requested_account_ids": ["ISA-1"],
                    "scope_refs": [ref],
                    "effective_date": "2026-04-30",
                    "narratives": [
                        {
                            "category": "objective",
                            "text": "Your objective is long-term flexibility.",
                            "refs": [ref],
                        }
                    ],
                    "actions": [
                        {
                            "action_id": "a",
                            "kind": "retain",
                            "source_account_id": "ISA-1",
                            "extent": "full",
                            "rationale": "Long-term flexibility",
                            "refs": [ref],
                        }
                    ],
                }
            ).model_dump(mode="json")
        elif task == "write":
            text = self.narrative
        elif task == "validate_support":
            data = {
                "supported": self.support,
                "issues": [] if self.support else ["unsupported claim"],
            }
        elif task == "inclusion":
            ref = request["context"]["facts"]["scope_refs"][0]["evidence_id"]
            data = {
                "decision": "unresolved",
                "evidence_ids": [ref],
                "reason": "Unknown rule evidence",
            }
        else:
            raise AssertionError(task)
        self.records.append({"task": task, "outcome": "success"})
        return Ok(ModelReply(text, data, {}, "fake"))


def configured_case(tmp_path):
    client = tmp_path / "client"
    client.mkdir()
    (client / "notes.txt").write_text(
        "Scope ISA-1. Retain the whole ISA. Your objective is long-term flexibility."
    )
    (client / "db.json").write_text(
        json.dumps(
            {
                "holders": {
                    "client": {
                        "name": "Sam",
                        "accounts": [
                            {
                                "account_id": "ISA-1",
                                "type": "ISA",
                                "owner": "Sam",
                                "platform": "Example",
                                "value": 52000,
                                "currency": "GBP",
                                "valuation_date": "2026-04-30",
                                "status": "open",
                            }
                        ],
                    }
                }
            }
        )
    )
    config = {
        "document_title": "Renamed report",
        "sections": [
            {
                "id": "z",
                "title": "Opening",
                "template": "Advice for <<scope>>.",
                "placeholders": {
                    "scope": {"renderer": "scope", "output_type": "phrase"}
                },
            },
            {
                "id": "a",
                "title": "Context",
                "template": "<<body>>\n\n<<holdings>>",
                "placeholders": {
                    "body": {
                        "selector": "background",
                        "prompt": "Write objective",
                        "output_type": "paragraph",
                    },
                    "holdings": {"renderer": "holdings", "output_type": "table"},
                },
            },
            {
                "id": "j",
                "title": "Next steps",
                "template": "<<actions>>",
                "placeholders": {
                    "actions": {"renderer": "actions", "output_type": "static"}
                },
            },
        ],
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    return client, path, tmp_path / "out", config


class RepairProvider(CaseProvider):
    def __init__(self, trigger, output, *, fail_after=False):
        super().__init__()
        self.trigger = trigger
        self.output = output
        self.fail_after = fail_after
        self.triggered = False

    def complete(self, **request):
        if self.triggered and self.fail_after:
            # Evidence must be on disk before the retry, not just at run completion.
            assert list(self.output.glob("client_repair_*.md"))
            return Err(ProviderError("unavailable", "Provider unavailable", "provider"))
        result = super().complete(**request)
        if self.triggered:
            return result
        if self.trigger == "reconcile" and request["task"] == "extract":
            result.value.data["actions"][0]["refs"][0]["excerpt"] = (
                "UNSUPPORTED-EXCERPT"
            )
        elif self.trigger == "shape" and request["task"] == "write":
            result = Ok(ModelReply("## Unexpected heading", None, {}, "fake"))
        elif (
            self.trigger in {"facts", "narrative"}
            and request["task"] == "validate_support"
        ):
            result.value.data.update(
                supported=False,
                issues=['The draft says "retire now"; evidence says "retire later".'],
                issue_kind=self.trigger,
            )
        else:
            return result
        self.triggered = True
        return result


class QueryProvider(CaseProvider):
    def complete(self, **request):
        result = super().complete(**request)
        if request["task"] == "extract":
            data = result.value.data
            data["review_items"] = [
                {"code": "missing", "message": "Confirm the missing rationale."},
                {
                    "code": "different",
                    "message": "Reconcile differing instructions.",
                    "category": "discrepancy",
                },
            ]
            return Ok(ModelReply("", data, {}, "fake"))
        return result
