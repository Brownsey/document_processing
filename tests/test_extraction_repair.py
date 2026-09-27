from dataclasses import replace

from test_workflow import CaseProvider, configured_case

from agent_pipeline.contracts import Err, Ok
from agent_pipeline.workflow import run_generation


def test_support_review_keeps_full_sources_without_repeating_quote_payloads(tmp_path):
    client, config, output, _ = configured_case(tmp_path)
    provider = CaseProvider()
    result = run_generation(
        client_dir=client, config_path=config, output_dir=output, provider=provider
    )
    assert isinstance(result, Ok)
    context = next(
        c["context"] for c in provider.calls if c["task"] == "validate_support"
    )
    original_ref = result.value["facts"]["scope_refs"][0]
    assert context["facts"]["scope_refs"] == [
        {"evidence_id": original_ref["evidence_id"]}
    ]
    assert original_ref["excerpt"]
    assert any(
        b["id"] == original_ref["evidence_id"] and original_ref["excerpt"] in b["text"]
        for b in context["evidence"]
    )
    assert all(set(n) == {"slot", "text"} for n in context["narratives"])
    assert context["report_stage"] == "adviser_review_draft"
    assert context["facts"]["actions"][0]["source_account_id"] == "ISA-1"


def test_model_uses_short_citation_ids_but_published_provenance_keeps_source_ids(
    tmp_path,
):
    client, config, output, _ = configured_case(tmp_path)
    provider = CaseProvider()
    result = run_generation(
        client_dir=client, config_path=config, output_dir=output, provider=provider
    )
    assert isinstance(result, Ok)
    ids = [b["id"] for b in provider.calls[0]["context"]["evidence"]]
    assert all(i.startswith("e") and len(i) <= 6 for i in ids)
    assert result.value["facts"]["scope_refs"][0]["evidence_id"].startswith("ev_")


def test_bad_citation_is_reextracted_and_revalidated_with_bounded_retries(tmp_path):
    client, config, output, _ = configured_case(tmp_path)

    class Provider(CaseProvider):
        def complete(self, **request):
            result = super().complete(**request)
            if request["task"] == "extract" and not request["context"].get("repair"):
                data = result.value.data
                data["scope_refs"][0]["excerpt"] = "invented quotation"
                return Ok(replace(result.value, data=data))
            return result

    provider = Provider()
    result = run_generation(
        client_dir=client, config_path=config, output_dir=output, provider=provider
    )
    assert isinstance(result, Ok), result
    assert len([r for r in provider.calls if r["task"] == "extract"]) == 2
    assert result.value["repair_counts"]["facts"] == 1


def test_permanently_bad_citation_still_blocks_after_two_repairs(tmp_path):
    client, config, output, _ = configured_case(tmp_path)

    class Provider(CaseProvider):
        def complete(self, **request):
            result = super().complete(**request)
            if request["task"] == "extract":
                data = result.value.data
                data["scope_refs"][0]["excerpt"] = "invented quotation"
                return Ok(replace(result.value, data=data))
            return result

    provider = Provider()
    result = run_generation(
        client_dir=client, config_path=config, output_dir=output, provider=provider
    )
    assert isinstance(result, Err)
    assert result.error.code == "unsupported_reference"
    assert len(provider.calls) == 3
    assert not (output / "client.md").exists()
