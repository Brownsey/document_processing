import importlib
import importlib.util
import json

import pytest


def experiments():
    assert importlib.util.find_spec("agent_pipeline.experiments") is not None, (
        "experiment workflow missing"
    )
    return importlib.import_module("agent_pipeline.experiments")


def test_missing_approval_stops_before_import_or_optimizer(tmp_path):
    module = experiments()
    with pytest.raises(ValueError, match="review"):
        module.require_approval({}, {"config_sha256": "abc", "prompts": {}})


def test_approval_binds_config_and_exact_registered_versions():
    module = experiments()
    registration = {
        "config_sha256": "abc",
        "prompts": {"extraction_prompt": "prompts:/extract/2"},
    }
    approval = {
        "review_status": "approved",
        "reviewed_by": "Stephen",
        "reviewed_at": "2026-09-26T20:00:00Z",
        "config_sha256": "abc",
        "prompt_versions": registration["prompts"],
    }
    module.require_approval(approval, registration)
    for field in ("config_sha256", "prompt_versions", "reviewed_by", "reviewed_at"):
        broken = dict(approval)
        broken[field] = ""
        with pytest.raises(ValueError):
            module.require_approval(broken, registration)


def test_allowlist_excludes_static_renderers():
    module = experiments()
    config = {
        "extraction_prompt": "extract",
        "sections": [
            {
                "id": "intro",
                "placeholders": {
                    "fixed": {"renderer": "scope", "prompt": "inactive"},
                    "explanation": {"prompt": "explain"},
                },
            }
        ],
    }
    assert module.active_prompts(config) == {
        "extraction_prompt": "extract",
        "sections.intro.explanation": "explain",
    }
    changed = module.replace_prompt(
        config, "sections.intro.explanation", "new explanation"
    )
    assert (
        changed["sections"][0]["placeholders"]["explanation"]["prompt"]
        == "new explanation"
    )
    assert config["sections"][0]["placeholders"]["explanation"]["prompt"] == "explain"
    with pytest.raises(ValueError):
        module.replace_prompt(config, "sections.intro.fixed", "mutate")


def test_reserved_families_rejected_before_optimization():
    module = experiments()
    with pytest.raises(ValueError, match="reserved"):
        module.development_rows(
            [{"partition": "reserved", "client": "secret", "family": "heldout"}]
        )


def test_real_local_registry_roundtrip(tmp_path):
    pytest.importorskip("mlflow")
    module = experiments()
    config = {"extraction_prompt": "Extract supported facts only.", "sections": []}
    registration = module.register_config(config, tmp_path, name="test_prompts")
    assert registration["review_status"] == "PENDING USER REVIEW"
    assert registration["prompts"]["extraction_prompt"].endswith("/1")
    import mlflow

    prompt = mlflow.genai.load_prompt(registration["prompts"]["extraction_prompt"])
    assert prompt.template == config["extraction_prompt"]
    assert (
        json.loads((tmp_path / "prompt-registration.json").read_text())["config_sha256"]
        == registration["config_sha256"]
    )


def test_optimization_entrypoint_cannot_bypass_review(tmp_path, monkeypatch):
    module = experiments()

    def forbidden_import(*args, **kwargs):
        raise AssertionError("MLflow setup reached without review")

    monkeypatch.setattr(module, "_local_mlflow", forbidden_import)
    with pytest.raises(ValueError, match="review"):
        module.optimize_reviewed(
            config={},
            registration={},
            approval={},
            prompt_key="extraction_prompt",
            expectations=[],
            data_dir=tmp_path,
            expectations_dir=tmp_path,
            directory=tmp_path,
        )


def test_base_pipeline_imports_without_mlflow():
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import agent_pipeline.generate, agent_pipeline.evaluation, agent_pipeline.experiments; import sys; assert 'mlflow' not in sys.modules",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_experiment_cli_loads_dotenv_before_optimize(tmp_path, monkeypatch):
    import os

    import dotenv

    module = experiments()
    marker = "EXPERIMENT_TEST_CREDENTIAL"
    monkeypatch.delenv(marker, raising=False)
    env = tmp_path / ".env"
    env.write_text(marker + "=synthetic-not-a-real-secret")
    actual_load = dotenv.load_dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda: actual_load(env))
    for name in ("config", "approval", "registration"):
        (tmp_path / (name + ".json")).write_text("{}")

    def optimize(**kwargs):
        assert os.getenv(marker) == "synthetic-not-a-real-secret"
        return {}

    monkeypatch.setattr(module, "optimize_reviewed", optimize)
    assert (
        module.main(
            [
                "--mode",
                "optimize",
                "--config",
                str(tmp_path / "config.json"),
                "--approval",
                str(tmp_path / "approval.json"),
                "--registration",
                str(tmp_path / "registration.json"),
            ]
        )
        == 0
    )


@pytest.mark.parametrize("separate", [False, True])
def test_finalist_cli_wires_review_judge_and_separate_models(
    tmp_path, monkeypatch, separate
):
    from types import SimpleNamespace

    import agent_pipeline.evaluation as evaluation
    import agent_pipeline.providers as providers
    from agent_pipeline.contracts import Ok

    module = experiments()
    for name in ("baseline", "finalist", "approval", "registration"):
        (tmp_path / (name + ".json")).write_text("{}")
    (tmp_path / "judge.json").write_text(
        json.dumps(
            {"prompt": "reviewed judge prompt", "rubric": {"review_status": "approved"}}
        )
    )
    (tmp_path / "judge.txt").write_text("reviewed judge prompt")
    (tmp_path / "rubric.json").write_text(json.dumps({"review_status": "approved"}))
    settings = []

    def provider(**kwargs):
        settings.append(kwargs)
        return Ok(SimpleNamespace(settings=kwargs))

    def compare(**kwargs):
        assert kwargs["baseline_config_path"] == tmp_path / "baseline.json"
        assert kwargs["finalist_config_path"] == tmp_path / "finalist.json"
        assert kwargs["judge_prompt"] == "reviewed judge prompt"
        kwargs["provider_factory"]()
        kwargs["judge_provider_factory"]()
        assert settings[0]["model"] == "gpt-6-luna"
        assert settings[1]["model"] == "offline-judge"
        return {
            "recommendation": "keep_baseline",
            "gaps": [],
            "output_path": "comparison.json",
        }

    monkeypatch.setattr(providers, "create_provider", provider)
    monkeypatch.setattr(evaluation, "compare_finalist", compare)
    assert (
        module.main(
            [
                "--mode",
                "compare-finalist",
                "--config",
                str(tmp_path / "baseline.json"),
                "--finalist-config",
                str(tmp_path / "finalist.json"),
                "--approval",
                str(tmp_path / "approval.json"),
                "--registration",
                str(tmp_path / "registration.json"),
                *(
                    [
                        "--judge-prompt",
                        str(tmp_path / "judge.txt"),
                        "--judge-rubric",
                        str(tmp_path / "rubric.json"),
                    ]
                    if separate
                    else ["--judge-config", str(tmp_path / "judge.json")]
                ),
                "--judge-model",
                "offline-judge",
            ]
        )
        == 0
    )
