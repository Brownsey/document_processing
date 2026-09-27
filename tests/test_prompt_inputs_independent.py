"""Independent checks for the exact facts passed to narrative prompts."""

from dataclasses import replace
from types import SimpleNamespace

from test_workflow import CaseProvider, configured_case

from agent_pipeline.contracts import Err, ModelReply, Ok, ProviderError
from agent_pipeline.domain import CaseFacts
from agent_pipeline.workflow import _structured, run_generation


def test_actual_extraction_schema_requires_every_field_and_forbids_extras():
    captured = []

    class Provider:
        def complete(self, **request):
            captured.append(request["schema"])
            return Err(
                ProviderError("offline_capture", "Stop after capturing the schema")
            )

    _structured(
        Provider(), schema=CaseFacts, task="extract", instructions="Extract", context={}
    )
    assert len(captured) == 1

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

    check(captured[0])


def test_integrated_background_prompt_receives_only_its_selected_projection(tmp_path):
    client, config, output, _ = configured_case(tmp_path)

    class Provider(CaseProvider):
        def complete(self, **request):
            result = super().complete(**request)
            if request["task"] == "extract":
                data = result.value.data
                data["review_items"] = [
                    {
                        "code": "confirm_balance",
                        "message": "Confirm account balance of £120,000 before transfer",
                    }
                ]
                data["narratives"][0]["text"] = (
                    "Your objective is long-term flexibility with £120,000."
                )
                return Ok(replace(result.value, data=data))
            return result

    provider = Provider()
    result = run_generation(
        client_dir=client, config_path=config, output_dir=output, provider=provider
    )

    assert isinstance(result, Ok), result
    write = next(call for call in provider.calls if call["task"] == "write")
    context = write["context"]
    assert set(context) == {"facts", "template", "slot", "output_type"}
    assert "review_items" not in context["facts"]
    assert "evidence" not in context and "internal_guidance" not in context
    assert "120,000" not in str(context["facts"])


def test_support_review_after_fact_repair_does_not_receive_old_verdict(tmp_path):
    client, config, output, _ = configured_case(tmp_path)

    class Provider(CaseProvider):
        def complete(self, **request):
            result = super().complete(**request)
            if (
                request["task"] == "validate_support"
                and sum(call["task"] == "validate_support" for call in self.calls) == 1
            ):
                return Ok(
                    replace(
                        result.value,
                        data={
                            "supported": False,
                            "issues": [
                                "Check the source again for a missing decision."
                            ],
                            "issue_kind": "facts",
                        },
                    )
                )
            return result

    provider = Provider()
    result = run_generation(
        client_dir=client, config_path=config, output_dir=output, provider=provider
    )
    assert isinstance(result, Ok), result
    extracts = [call for call in provider.calls if call["task"] == "extract"]
    reviews = [call for call in provider.calls if call["task"] == "validate_support"]
    assert len(extracts) == len(reviews) == 2
    assert "repair" in extracts[1]["context"]
    assert "repair" not in reviews[1]["context"]
    assert reviews[1]["context"]["evidence"]
    assert reviews[1]["context"]["facts"]
    assert reviews[1]["context"]["report"]


def test_citation_aliases_never_rewrite_account_ids_or_quoted_text():
    def complete(**request):
        assert request["context"]["evidence"][0]["id"] == "e1"
        assert request["context"]["client_scope"] == "ev_original_digest"
        assert request["context"]["focused_evidence"][0]["result"][0]["id"] == "e1"
        assert (
            request["context"]["focused_evidence"][1]["result"][0]["account_id"]
            == "ev_original_digest"
        )
        return Ok(
            ModelReply(
                "",
                {
                    "requested_account_ids": ["e1"],
                    "scope_refs": [{"evidence_id": "e1", "excerpt": "e1"}],
                    "planned_accounts": [{"account_id": "e1"}],
                },
                {},
                "fake",
            )
        )

    result = _structured(
        SimpleNamespace(complete=complete),
        schema=CaseFacts,
        task="extract",
        instructions="Extract facts",
        context={
            "evidence": [{"id": "ev_original_digest", "text": "e1"}],
            "client_scope": "ev_original_digest",
            "focused_evidence": [
                {
                    "tool": "read",
                    "result": [{"id": "ev_original_digest", "text": "e1"}],
                },
                {"tool": "account", "result": [{"account_id": "ev_original_digest"}]},
            ],
        },
    )
    assert isinstance(result, Ok), result
    assert result.value.requested_account_ids == ["e1"]
    assert result.value.planned_accounts[0].account_id == "e1"
    assert result.value.scope_refs[0].evidence_id == "ev_original_digest"
    assert result.value.scope_refs[0].excerpt == "e1"
