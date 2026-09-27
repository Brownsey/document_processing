import pytest


def test_unknown_selector_rejected_before_generation():
    from agent_pipeline.contracts import Err
    from agent_pipeline.workflow import validate_config

    result = validate_config(
        {
            "document_title": "Report",
            "sections": [
                {
                    "id": "renamed",
                    "title": "Any",
                    "template": "<<text>>",
                    "placeholders": {
                        "text": {"selector": "invented", "prompt": "Write"}
                    },
                }
            ],
        }
    )
    assert isinstance(result, Err)
    assert result.error.code == "invalid_config"


@pytest.mark.parametrize("change", ["missing", "extra", "duplicate"])
def test_slot_contract_rejects_mismatched_or_duplicate_fields(change):
    from agent_pipeline.contracts import Err
    from agent_pipeline.workflow import validate_config

    slot = {"prompt": "Write"}
    section = {
        "id": "one",
        "title": "One",
        "template": "<<text>>",
        "placeholders": {"text": slot},
    }
    if change == "missing":
        section["placeholders"] = {}
    elif change == "extra":
        section["placeholders"]["other"] = slot
    else:
        section["template"] += " <<text>>"
    assert isinstance(
        validate_config({"document_title": "Report", "sections": [section]}), Err
    )


class FailingProvider:
    def __init__(self, error):
        self.error = error
        self.calls = []
        self.usage_records = []

    def complete(self, **kwargs):
        from agent_pipeline.contracts import Err

        self.calls.append(kwargs)
        return Err(self.error)


def test_failed_rerun_removes_stale_report_and_preserves_error(tmp_path):
    import json

    from agent_pipeline.contracts import Err, ProviderError
    from agent_pipeline.workflow import run_generation

    client = tmp_path / "client"
    client.mkdir()
    (client / "notes.txt").write_text("Discussed ISA with Sam.")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "document_title": "Report",
                "sections": [
                    {
                        "id": "one",
                        "title": "One",
                        "template": "<<text>>",
                        "placeholders": {"text": {"prompt": "Write"}},
                    }
                ],
            }
        )
    )
    output = tmp_path / "outputs"
    output.mkdir()
    (output / "client.md").write_text("stale success")
    error = ProviderError("denied", "Denied", "extract")
    provider = FailingProvider(error)
    result = run_generation(
        client_dir=client, config_path=config, output_dir=output, provider=provider
    )
    assert isinstance(result, Err) and result.error is error
    assert not (output / "client.md").exists()
    manifest = json.loads((output / "client.manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["error"]["code"] == "denied"
    assert len(provider.calls) == 1


def test_invalid_config_causes_zero_provider_calls(tmp_path):
    from agent_pipeline.contracts import Err, ProviderError
    from agent_pipeline.workflow import run_generation

    config = tmp_path / "config.json"
    config.write_text("{}")
    provider = FailingProvider(ProviderError("unexpected", "must not call"))
    result = run_generation(
        client_dir=tmp_path / "missing",
        config_path=config,
        output_dir=tmp_path / "out",
        provider=provider,
    )
    assert isinstance(result, Err)
    assert result.error.code == "invalid_config"
    assert not provider.calls


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
        from agent_pipeline.contracts import ModelReply, Ok

        self.calls.append(request)
        if self.interrupt:
            raise KeyboardInterrupt
        task = request["task"]
        data = None
        text = ""
        if task == "extract":
            from agent_pipeline.domain import CaseFacts

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
        self.records.append(
            {"task": task, "outcome": "success", "estimated_cost_usd": 0.001}
        )
        return Ok(ModelReply(text, data, {}, "fake"))


def configured_case(tmp_path):
    import json

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


def test_real_pipeline_success_has_scoped_facts_provenance_usage_and_fingerprints(
    tmp_path,
):
    from pathlib import Path

    from agent_pipeline.contracts import Ok
    from agent_pipeline.workflow import run_generation

    client, config, output, _ = configured_case(tmp_path)
    provider = CaseProvider()
    result = run_generation(
        client_dir=client, config_path=config, output_dir=output, provider=provider
    )
    assert isinstance(result, Ok), result
    manifest = result.value
    text = Path(manifest["output_path"]).read_text(encoding="utf-8")
    assert (
        text.index("## Opening")
        < text.index("## Context")
        < text.index("## Next steps")
    )
    assert "£52,000 (30 April 2026)" in text
    assert manifest["status"] == "needs_review"
    assert manifest["facts"]["requested_account_ids"] == ["ISA-1"]
    assert manifest["facts"]["scope_refs"][0]["excerpt"].startswith("Scope")
    assert len(manifest["usage"]) == 3
    assert set(manifest["fingerprints"]) >= {
        "inputs",
        "config",
        "code",
        "prompts",
        "report",
    }


@pytest.mark.parametrize("mode", ["repair", "support", "interrupt", "inclusion"])
def test_failed_generation_never_publishes_report(tmp_path, mode):
    import json

    from agent_pipeline.contracts import Err
    from agent_pipeline.workflow import run_generation

    client, config, output, value = configured_case(tmp_path)
    provider = CaseProvider(
        narrative="## Wrong heading"
        if mode == "repair"
        else "Your objective is long-term flexibility.",
        support=mode != "support",
        interrupt=mode == "interrupt",
    )
    if mode == "inclusion":
        value["sections"][0]["use_if"] = "Only if cash is sufficient"
        config.write_text(json.dumps(value))
    result = run_generation(
        client_dir=client, config_path=config, output_dir=output, provider=provider
    )
    assert isinstance(result, Err)
    assert not (output / "client.md").exists()
    manifest = json.loads((output / "client.manifest.json").read_text())
    assert manifest["status"] in {"failed", "blocked"}
    if mode == "repair":
        assert len([c for c in provider.calls if c["task"] == "write"]) == 3
    if mode == "interrupt":
        assert result.error.code == "interrupted"
    if mode == "inclusion":
        assert result.error.code == "unresolved_inclusion"


def test_prompt_only_config_receives_full_facts_and_changed_prompt(tmp_path):
    import json

    from agent_pipeline.contracts import Ok
    from agent_pipeline.workflow import run_generation

    client, config, output, value = configured_case(tmp_path)
    value["sections"][1]["placeholders"]["body"] = {"prompt": "Changed trusted prompt"}
    config.write_text(json.dumps(value))
    provider = CaseProvider()
    result = run_generation(
        client_dir=client, config_path=config, output_dir=output, provider=provider
    )
    assert isinstance(result, Ok), result
    write = next(c for c in provider.calls if c["task"] == "write")
    assert "Changed trusted prompt" in write["instructions"]
    assert write["context"]["facts"]["actions"][0]["action_id"] == "a"
    assert result.value["diagnostics"] == [
        {"code": "legacy_full_facts", "slot": "body"}
    ]


def test_prompt_only_table_keeps_legacy_output_shape(tmp_path):
    import json

    from agent_pipeline.contracts import Ok
    from agent_pipeline.workflow import run_generation

    client, config, output, value = configured_case(tmp_path)
    value["sections"] = [
        {
            "id": "custom",
            "title": "Accounts",
            "template": "<<holdings>>",
            "placeholders": {
                "holdings": {"prompt": "List scoped accounts as a markdown table."}
            },
        }
    ]
    config.write_text(json.dumps(value))
    provider = CaseProvider(
        narrative="| Account | Owner | Type | Value |\n|---|---|---|---|\n| ISA-1 | Sam | ISA | £52,000 (30 April 2026) |"
    )
    result = run_generation(
        client_dir=client, config_path=config, output_dir=output, provider=provider
    )
    assert isinstance(result, Ok), result


def test_investigation_repeated_reads_stop_and_cannot_read_paths():
    from agent_pipeline.contracts import EvidenceBlock, EvidenceBundle, ModelReply, Ok
    from agent_pipeline.workflow import _investigate

    class Reader:
        def complete(self, **kwargs):
            return Ok(
                ModelReply(
                    "",
                    {
                        "tool": "read",
                        "argument": "../other-client/secret.txt",
                        "reason": "look",
                    },
                    {},
                    "fake",
                )
            )

    trace = []
    bundle = EvidenceBundle(
        [EvidenceBlock("safe", "notes.txt", "paragraph 1", "own facts", "hash")], [], []
    )
    result = _investigate({}, bundle, Reader(), {}, trace)
    assert isinstance(result, Ok)
    assert result.value == []
    assert trace[-1]["stopping_reason"] == "no_new_evidence"


def test_cli_missing_credentials_is_nonzero_and_clears_stale_report(
    tmp_path, monkeypatch
):
    from agent_pipeline import generate
    from agent_pipeline.contracts import ConfigError, Err

    client, config, output, _ = configured_case(tmp_path)
    output.mkdir()
    (output / "client.md").write_text("stale report")
    monkeypatch.setattr(
        generate,
        "create_provider",
        lambda **kwargs: Err(ConfigError("missing_credentials", "Missing", "provider")),
    )
    code = generate.main(
        [
            "--client",
            client.name,
            "--data-dir",
            str(client.parent),
            "--config",
            str(config),
            "--output-dir",
            str(output),
        ]
    )
    assert code == 1
    assert not (output / "client.md").exists()


@pytest.mark.parametrize(
    "field,value",
    [("placeholders", []), ("template", None), ("title", "Injected\n## Heading")],
)
def test_invalid_local_template_fields_fail_preflight(field, value):
    from agent_pipeline.contracts import Err
    from agent_pipeline.workflow import validate_config

    config = {
        "document_title": "Report",
        "sections": [
            {"id": "s", "title": "Title", "template": "Static", "placeholders": {}}
        ],
    }
    config["sections"][0][field] = value
    result = validate_config(config)
    assert isinstance(result, Err)
    assert result.error.code == "invalid_config"


def test_reused_provider_usage_is_scoped_to_each_run_and_input_changes_fingerprint(
    tmp_path,
):
    from agent_pipeline.contracts import Ok
    from agent_pipeline.workflow import run_generation

    client, config, output, _ = configured_case(tmp_path)
    provider = CaseProvider()
    first = run_generation(
        client_dir=client, config_path=config, output_dir=output, provider=provider
    )
    assert isinstance(first, Ok)
    (client / "notes.txt").write_text(
        "Scope ISA-1. Retain the whole ISA. Your objective is long-term flexibility. Additional note."
    )
    second = run_generation(
        client_dir=client, config_path=config, output_dir=output, provider=provider
    )
    assert isinstance(second, Ok)
    assert len(second.value["usage"]) == 3
    assert (
        first.value["fingerprints"]["inputs"] != second.value["fingerprints"]["inputs"]
    )
    assert second.value["model"] == "fake"


def test_budget_exhaustion_does_not_call_dependent_provider():
    from agent_pipeline.contracts import Err, ProviderError
    from agent_pipeline.workflow import BudgetPort

    provider = FailingProvider(ProviderError("original", "Original"))
    port = BudgetPort(provider, max_calls=0)
    result = port.complete(task="extract", instructions="", context={})
    assert isinstance(result, Err)
    assert result.error.code == "execution_limit"
    assert provider.calls == []


def test_empty_scope_blocks_report_before_writing(tmp_path):
    from agent_pipeline.contracts import Err, ModelReply, Ok
    from agent_pipeline.workflow import run_generation

    class EmptyScope(CaseProvider):
        def complete(self, **kwargs):
            result = super().complete(**kwargs)
            if kwargs["task"] == "extract":
                data = dict(result.value.data)
                data["requested_account_ids"] = []
                return Ok(ModelReply("", data, {}, "fake"))
            return result

    client, config, output, _ = configured_case(tmp_path)
    provider = EmptyScope()
    result = run_generation(
        client_dir=client, config_path=config, output_dir=output, provider=provider
    )
    assert isinstance(result, Err)
    assert result.error.code == "missing_scope"
    assert not any(call["task"] == "write" for call in provider.calls)


def test_error_manifest_preserves_safe_source_and_check_context(tmp_path):
    import json

    from agent_pipeline.contracts import ProviderError
    from agent_pipeline.workflow import run_generation

    client, config, output, _ = configured_case(tmp_path)
    error = ProviderError(
        "source_failed",
        "Safe message",
        "extract",
        details={"source": "notes.txt", "checks": ["schema"]},
    )
    run_generation(
        client_dir=client,
        config_path=config,
        output_dir=output,
        provider=FailingProvider(error),
    )
    manifest = json.loads((output / "client.manifest.json").read_text())
    assert manifest["error"]["details"] == {"source": "notes.txt", "checks": ["schema"]}


def test_budget_port_sets_absolute_provider_deadline():
    import time

    from agent_pipeline.workflow import BudgetPort

    class DeadlineProvider:
        def set_deadline(self, deadline):
            self.deadline = deadline

        def complete(self, **kwargs):
            raise AssertionError("Setting a deadline must not call the provider")

    provider = DeadlineProvider()
    before = time.monotonic()
    BudgetPort(provider, timeout=12)
    assert before + 12 <= provider.deadline <= time.monotonic() + 12


@pytest.mark.parametrize(
    "issue_kind,extraction_count", [("facts", 2), ("narrative", 1)]
)
def test_support_repair_routes_to_facts_or_fixed_fact_narrative(
    tmp_path, monkeypatch, issue_kind, extraction_count
):
    from pathlib import Path

    from agent_pipeline import evidence
    from agent_pipeline.contracts import ModelReply, Ok
    from agent_pipeline.workflow import run_generation

    loads = []
    original_load = evidence.load_sources

    def load_once(*args, **kwargs):
        loads.append(1)
        return original_load(*args, **kwargs)

    monkeypatch.setattr(evidence, "load_sources", load_once)

    class RepairingProvider(CaseProvider):
        def __init__(self):
            super().__init__()
            self.reviews = 0
            self.approved = []

        def approve_cache(self, reply):
            self.approved.append(reply)

        def complete(self, **request):
            result = super().complete(**request)
            if (
                request["task"] == "extract"
                and issue_kind == "facts"
                and self.reviews == 0
            ):
                result.value.data["actions"][0]["rationale"] = "Unsupported rationale"
            if request["task"] == "write" and self.reviews == 0:
                return Ok(ModelReply("Obsolete draft narrative.", None, {}, "fake"))
            if request["task"] == "validate_support":
                self.reviews += 1
                return Ok(
                    ModelReply(
                        "",
                        {
                            "supported": self.reviews > 1,
                            "issues": ["Unsupported statement"]
                            if self.reviews == 1
                            else [],
                            "issue_kind": issue_kind if self.reviews == 1 else "none",
                        },
                        {},
                        "fake",
                    )
                )
            return result

    client, config, output, _ = configured_case(tmp_path)
    provider = RepairingProvider()
    result = run_generation(
        client_dir=client, config_path=config, output_dir=output, provider=provider
    )
    assert isinstance(result, Ok), result
    assert len(loads) == 1
    assert (
        len([c for c in provider.calls if c["task"] == "extract"]) == extraction_count
    )
    writes = [c for c in provider.calls if c["task"] == "write"]
    assert len(writes) == 2
    if issue_kind == "narrative":
        assert writes[0]["context"]["facts"] == writes[1]["context"]["facts"]
        assert writes[1]["context"]["repair"]["fixed_facts"] is True
    report = Path(result.value["output_path"]).read_text()
    assert "Obsolete" not in report and "Unsupported rationale" not in report
    assert len(provider.approved) == 1
    assert result.value["repair_counts"][issue_kind] == 1


@pytest.mark.parametrize("publish_anyway", [False, True])
@pytest.mark.parametrize("issue_kind", ["facts", "narrative"])
def test_support_repairs_exhaust_two_attempts_without_publication(
    tmp_path, issue_kind, publish_anyway
):
    from agent_pipeline.contracts import Err, ModelReply, Ok
    from agent_pipeline.workflow import run_generation

    class RejectingProvider(CaseProvider):
        def __init__(self):
            super().__init__()
            self.approved = []

        def approve_cache(self, reply):
            self.approved.append(reply)

        def complete(self, **request):
            result = super().complete(**request)
            if request["task"] == "validate_support":
                return Ok(
                    ModelReply(
                        "",
                        {
                            "supported": False,
                            "issues": ["Unsupported statement"],
                            "issue_kind": issue_kind,
                        },
                        {},
                        "fake",
                    )
                )
            return result

    client, config, output, _ = configured_case(tmp_path)
    provider = RejectingProvider()
    result = run_generation(
        client_dir=client,
        config_path=config,
        output_dir=output,
        provider=provider,
        publish_anyway=publish_anyway,
    )
    if publish_anyway:
        assert isinstance(result, Ok)
        manifest = result.value
        assert manifest["status"] == "published_with_issues"
        assert manifest["published_anyway"] is True
        assert manifest["validation"]["passed"] is False
        assert manifest["error"]["details"]["issues"] == ["Unsupported statement"]
        report = (output / "client.md").read_text(encoding="utf-8")
        assert "MANUAL CORRECTION REQUIRED" in report
        assert "Unsupported statement" in report
        assert "## Next steps" in report
        assert not any(key.startswith("_") for key in manifest)
    else:
        assert isinstance(result, Err) and result.error.code == "unsupported_report"
        assert not (output / "client.md").exists()
    assert len([c for c in provider.calls if c["task"] == "validate_support"]) == 3
    assert provider.approved == []


def test_shape_and_semantic_narrative_repairs_share_two_attempts(tmp_path):
    from agent_pipeline.contracts import Err, ModelReply, Ok
    from agent_pipeline.workflow import run_generation

    class BadNarrative(CaseProvider):
        def complete(self, **request):
            result = super().complete(**request)
            if (
                request["task"] == "write"
                and len([c for c in self.calls if c["task"] == "write"]) == 1
            ):
                return Ok(ModelReply("## Wrong heading", None, {}, "fake"))
            if request["task"] == "validate_support":
                return Ok(
                    ModelReply(
                        "",
                        {
                            "supported": False,
                            "issues": ["Unsupported prose"],
                            "issue_kind": "narrative",
                        },
                        {},
                        "fake",
                    )
                )
            return result

    client, config, output, _ = configured_case(tmp_path)
    provider = BadNarrative()
    result = run_generation(
        client_dir=client, config_path=config, output_dir=output, provider=provider
    )
    assert isinstance(result, Err) and result.error.code == "unsupported_report"
    assert len([c for c in provider.calls if c["task"] == "write"]) == 3
    assert len([c for c in provider.calls if c["task"] == "extract"]) == 1
    assert not (output / "client.md").exists()


def test_openrouter_requires_explicit_model_before_provider_setup(monkeypatch):
    from agent_pipeline import generate

    calls = []
    monkeypatch.setenv("OPENAI_MODEL", "openai-only-model")
    monkeypatch.setattr(
        generate, "create_provider", lambda **kwargs: calls.append(kwargs)
    )
    with pytest.raises(SystemExit) as error:
        generate.main(["--client", "client", "--provider", "openrouter"])
    assert error.value.code == 2
    assert calls == []


def test_original_client_only_cli_keeps_defaults_and_loads_dotenv(
    tmp_path, monkeypatch
):
    """Exercise the unchanged entry point without discovering a real checkout secret."""
    import importlib.util
    import os
    import shutil
    from pathlib import Path

    from agent_pipeline import generate
    from agent_pipeline.contracts import Ok

    # Dotenv searches upward from the calling module. An unchanged temporary copy
    # makes that real lookup isolated, without replacing dotenv or its loader.
    isolated_module = tmp_path / "src" / "agent_pipeline" / "generate.py"
    isolated_module.parent.mkdir(parents=True)
    shutil.copyfile(generate.__file__, isolated_module)
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=offline-placeholder-key\nOPENAI_MODEL=offline-placeholder-model\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    specification = importlib.util.spec_from_file_location(
        "isolated_generate", isolated_module
    )
    assert specification is not None and specification.loader is not None
    isolated = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(isolated)
    calls = {}
    fake_provider = object()

    def provider_factory(**kwargs):
        calls["provider"] = kwargs
        calls["key"] = os.getenv("OPENAI_API_KEY")
        return Ok(fake_provider)

    def offline_workflow(**kwargs):
        calls["workflow"] = kwargs
        output = kwargs["output_dir"] / f"{kwargs['client_dir'].name}.md"
        output.parent.mkdir(parents=True)
        output.write_text("Offline compatibility draft.\n", encoding="utf-8")
        return Ok({"output_path": str(output)})

    monkeypatch.setattr(isolated, "create_provider", provider_factory)
    monkeypatch.setattr(isolated, "run_generation", offline_workflow)
    assert isolated.main(["--client", "client_01_clean"]) == 0
    assert calls["key"] == "offline-placeholder-key"
    assert calls["provider"] == {
        "provider": "openai",
        "model": "offline-placeholder-model",
        "base_url": None,
        "cache_dir": None,
        "timeout": 90,
        "max_retries": 2,
        "max_output_tokens": 20000,
        "reasoning_effort": "medium",
    }
    assert calls["workflow"] == {
        "client_dir": Path("data/client_01_clean"),
        "config_path": Path("config/template_config.json"),
        "output_dir": Path("outputs"),
        "provider": fake_provider,
        "run_timeout": 300,
        "max_calls": 40,
        "tone_of_voice": None,
        "adviser_confirmations": None,
        "publish_anyway": False,
    }
    assert (
        tmp_path / "outputs" / "client_01_clean.md"
    ).read_text() == "Offline compatibility draft.\n"
