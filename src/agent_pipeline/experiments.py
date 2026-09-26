"""Optional local MLflow registry/logging and explicitly reviewed prompt optimisation.

Git configuration is authoritative. Registration is not approval; optimisation
requires a separate user-authored review record bound to exact content/versions.
"""

from __future__ import annotations

import argparse
import copy
import importlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from agent_pipeline.evaluation import evaluate_case, fingerprint, summarise_runs


def active_prompts(config: dict) -> dict[str, str]:
    prompts = {
        key: value
        for key, value in config.items()
        if key.endswith("_prompt") and isinstance(value, str)
    }
    for section in config.get("sections", []):
        for name, slot in section.get("placeholders", {}).items():
            if slot.get("prompt") and not slot.get("renderer"):
                prompts[f"sections.{section['id']}.{name}"] = slot["prompt"]
    return prompts


def replace_prompt(config: dict, key: str, text: str) -> dict:
    if (
        key not in active_prompts(config)
        or not isinstance(text, str)
        or not text.strip()
    ):
        raise ValueError("Only allowlisted active prompt text may change.")
    candidate = copy.deepcopy(config)
    if key.startswith("sections."):
        _, section_id, slot = key.split(".", 2)
        section = next(s for s in candidate["sections"] if s["id"] == section_id)
        section["placeholders"][slot]["prompt"] = text
    else:
        candidate[key] = text
    return candidate


def _local_mlflow(directory: Path) -> Any:
    try:
        mlflow = importlib.import_module("mlflow")
    except ImportError as exc:
        raise RuntimeError(
            "MLflow is optional; install the locked experiment extra for this command."
        ) from exc
    directory.mkdir(parents=True, exist_ok=True)
    uri = "sqlite:///" + (directory.resolve() / "mlflow.db").as_posix()
    mlflow.set_tracking_uri(uri)
    mlflow.set_registry_uri(uri)
    mlflow.set_experiment("advice-report-prompts")
    return mlflow


def register_config(
    config: dict, directory: Path, *, name: str = "advice_report"
) -> dict:
    mlflow = _local_mlflow(directory)
    digest = fingerprint(config)
    prompts = {}
    for key, text in active_prompts(config).items():
        version = mlflow.genai.register_prompt(
            name=re.sub(r"[^A-Za-z0-9_]", "_", f"{name}_{key}"),
            template=text,
            commit_message="Git configuration registered for review",
            tags={
                "config_sha256": digest,
                "review_status": "pending_user_review",
                "slot": key,
            },
        )
        prompts[key] = version.uri
    registration = {
        "config_sha256": digest,
        "prompts": prompts,
        "review_status": "PENDING USER REVIEW",
        "mlflow_version": mlflow.__version__,
    }
    (directory / "prompt-registration.json").write_text(
        json.dumps(registration, indent=2), encoding="utf-8"
    )
    return registration


def require_approval(approval: dict, registration: dict) -> None:
    valid = (
        approval.get("review_status") == "approved"
        and bool(approval.get("reviewed_by"))
        and bool(approval.get("reviewed_at"))
        and approval.get("config_sha256") == registration.get("config_sha256")
        and approval.get("prompt_versions") == registration.get("prompts")
        and bool(registration.get("prompts"))
    )
    if not valid:
        raise ValueError(
            "User review is required for this exact configuration and registered prompt versions before optimisation."
        )
    if any(
        not re.fullmatch(r"prompts:/[^/]+/\d+", uri)
        for uri in registration["prompts"].values()
    ):
        raise ValueError(
            "Approval requires immutable numbered prompt versions, not aliases."
        )


def development_rows(expectations: list[dict]) -> list[dict]:
    partitions = json.loads(
        (Path(__file__).resolve().parents[2] / "eval" / "partitions.json").read_text(
            encoding="utf-8"
        )
    )
    reserved_families = {row["family"] for row in partitions["reserved_families"]}
    if any(
        row.get("partition") != "development" or row.get("family") in reserved_families
        for row in expectations
    ):
        raise ValueError(
            "Only development data may be tuned; reserved families must remain outside feedback."
        )
    return [
        {"inputs": {"client": row["client"]}, "expectations": row}
        for row in expectations
    ]


def log_evaluation(summary: dict, directory: Path) -> str:
    mlflow = _local_mlflow(directory / "tracking")
    with mlflow.start_run(run_name="report-comparison") as run:
        mlflow.set_tags(
            {"review_status": "pending_user_review", "judge_status": "not_run"}
        )
        for variant, result in summary.get("variants", {}).items():
            mlflow.log_metrics(
                {
                    f"{variant}.{key}": value
                    for key, value in result.items()
                    if isinstance(value, (int, float)) and not isinstance(value, bool)
                }
            )
        mlflow.log_dict(summary, "comparison.json")
        for record in summary.get("runs", []):
            path = record.get("manifest", {}).get("output_path")
            if path and Path(path).is_file():
                mlflow.log_artifact(
                    path,
                    artifact_path=f"reports/{record.get('variant', 'candidate')}/{record.get('client', 'case')}/{record.get('evaluation_id', 'run')}",
                )
        return run.info.run_id


def optimize_reviewed(
    *,
    config: dict,
    registration: dict,
    approval: dict,
    prompt_key: str,
    expectations: list[dict],
    data_dir: Path,
    expectations_dir: Path,
    directory: Path,
    report_model: str = "gpt-6-luna",
    optimizer_model: str = "openai:/gpt-6-astra",
    max_metric_calls: int = 8,
) -> dict:
    """One bounded family; never called automatically by evaluation/registration."""
    require_approval(approval, registration)
    if fingerprint(config) != registration["config_sha256"]:
        raise ValueError("Reviewed configuration changed; a new review is required.")
    if (
        prompt_key not in active_prompts(config)
        or prompt_key not in registration["prompts"]
    ):
        raise ValueError("Prompt is outside the approved active allowlist.")
    rows = development_rows(expectations)
    if not rows or not 1 <= max_metric_calls <= 16:
        raise ValueError("Use development examples and 1–16 metric calls.")
    mlflow = _local_mlflow(directory)
    from mlflow.genai.optimize import GepaPromptOptimizer
    from mlflow.genai.scorers import scorer

    from agent_pipeline.contracts import Err
    from agent_pipeline.providers import create_provider

    uri = registration["prompts"][prompt_key]
    runs: list[dict] = []

    def predict_fn(client: str) -> dict:
        # MLflow substitutes candidate PromptVersions during optimisation.
        prompt = mlflow.genai.load_prompt(uri)
        candidate = replace_prompt(config, prompt_key, prompt.template)
        run_dir = directory / "trials" / uuid.uuid4().hex
        run_dir.mkdir(parents=True)
        config_path = run_dir / "config.json"
        config_path.write_text(json.dumps(candidate, indent=2), encoding="utf-8")
        provider = create_provider(model=report_model)
        if isinstance(provider, Err):
            result = {
                "passed": False,
                "score": 0.0,
                "failures": [provider.error.code],
                "usage": [],
            }
        else:
            result = evaluate_case(
                client_dir=data_dir / client,
                config_path=config_path,
                expected_path=expectations_dir / f"{client}.json",
                output_dir=run_dir,
                provider=provider.value,
            )
        runs.append(result)
        return {
            "passed": result["passed"],
            "score": result["score"],
            "failures": result["failures"],
        }

    @scorer
    def hard_correctness(outputs: dict) -> float:
        return float(outputs["score"]) if outputs["passed"] else 0.0

    with mlflow.start_run(run_name="reviewed-single-family-optimisation"):
        mlflow.log_params(
            {
                "report_model": report_model,
                "optimizer_model": optimizer_model,
                "max_metric_calls": max_metric_calls,
                "prompt_key": prompt_key,
                "baseline_config_sha256": registration["config_sha256"],
            }
        )
        try:
            result = mlflow.genai.optimize_prompts(
                predict_fn=predict_fn,
                train_data=rows,
                prompt_uris=[uri],
                optimizer=GepaPromptOptimizer(
                    reflection_model=optimizer_model, max_metric_calls=max_metric_calls
                ),
                scorers=[hard_correctness],
            )
            finalists = [
                {"uri": p.uri, "template": p.template} for p in result.optimized_prompts
            ]
            for index, finalist in enumerate(finalists, start=1):
                path = directory / f"finalist-config-{index}.json"
                path.write_text(
                    json.dumps(
                        replace_prompt(config, prompt_key, finalist["template"]),
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                finalist["config_path"] = str(path.resolve())
        finally:
            # GEPA's reflection calls are outside our provider; never claim zero cost.
            ledger = {
                "generation": summarise_runs(runs),
                "optimizer_cost_usd": None,
                "optimizer_cost_status": "unknown: inspect MLflow optimizer traces/provider billing",
                "total_cost_per_valid_draft_usd": None,
                "runs": runs,
            }
            (directory / "optimisation-ledger.json").write_text(
                json.dumps(ledger, indent=2, default=str), encoding="utf-8"
            )
            mlflow.log_dict(ledger, "optimisation-ledger.json")
        output = {
            "baseline": registration,
            "finalists": finalists,
            "promotion": "not_evaluated",
            "review_status": "PENDING USER REVIEW",
            "required_next_step": "Two fresh Luna runs per development and reserved case; independent advisory/human review; no automatic promotion.",
            "cost": ledger,
        }
        (directory / "optimisation-result.json").write_text(
            json.dumps(output, indent=2, default=str), encoding="utf-8"
        )
        return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=["register", "optimize", "compare-finalist"],
        default="register",
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path(".local/experiments"))
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--registration", type=Path)
    parser.add_argument("--prompt-key", default="extraction_prompt")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--expectations-dir", type=Path, default=Path("eval/development")
    )
    parser.add_argument("--max-metric-calls", type=int, default=8)
    parser.add_argument("--finalist-config", type=Path)
    parser.add_argument("--judge-prompt", type=Path)
    parser.add_argument("--judge-rubric", type=Path)
    parser.add_argument(
        "--judge-config",
        type=Path,
        help="JSON containing prompt and a reviewed rubric.",
    )
    parser.add_argument(
        "--reserved-index", type=Path, default=Path("eval/partitions.json")
    )
    parser.add_argument("--report-model", default="gpt-6-luna")
    parser.add_argument("--judge-model", default="gpt-6-luna")
    parser.add_argument("--optimizer-model", default="openai:/gpt-6-astra")
    parser.add_argument("--optimizer-cost-usd", type=float, default=None)
    args = parser.parse_args(argv)
    from dotenv import load_dotenv

    load_dotenv()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.mode == "register":
        register_config(config, args.output_dir)
        print(str(args.output_dir / "prompt-registration.json"))
        return 0
    if not args.approval or not args.registration:
        parser.error(
            "Post-review modes require --approval and --registration after explicit user review."
        )
    try:
        if args.mode == "compare-finalist":
            from agent_pipeline.evaluation import compare_finalist
            from agent_pipeline.providers import create_provider

            if args.finalist_config is None:
                parser.error("Finalist comparison requires --finalist-config.")
            judge = (
                json.loads(args.judge_config.read_text(encoding="utf-8"))
                if args.judge_config
                else {}
            )
            if args.judge_config and (args.judge_prompt or args.judge_rubric):
                parser.error(
                    "Use --judge-config or the --judge-prompt/--judge-rubric pair."
                )
            if bool(args.judge_prompt) != bool(args.judge_rubric):
                parser.error("Supply both --judge-prompt and --judge-rubric.")
            if args.judge_prompt and args.judge_rubric:
                judge = {
                    "prompt": args.judge_prompt.read_text(encoding="utf-8"),
                    "rubric": json.loads(args.judge_rubric.read_text(encoding="utf-8")),
                }
            result = compare_finalist(
                baseline_config_path=args.config,
                finalist_config_path=args.finalist_config,
                registration=json.loads(args.registration.read_text(encoding="utf-8")),
                approval=json.loads(args.approval.read_text(encoding="utf-8")),
                data_dir=args.data_dir,
                expectations_dir=args.expectations_dir,
                output_dir=args.output_dir,
                provider_factory=lambda: create_provider(
                    model=args.report_model, cache_dir=None
                ),
                judge_provider_factory=(
                    lambda: create_provider(model=args.judge_model, cache_dir=None)
                )
                if judge
                else None,
                judge_prompt=judge.get("prompt"),
                rubric=judge.get("rubric"),
                reserved_index=args.reserved_index,
                optimizer_cost_usd=args.optimizer_cost_usd,
            )
            print(result["output_path"])
            return 1 if result["gaps"] else 0
        optimize_reviewed(
            config=config,
            registration=json.loads(args.registration.read_text(encoding="utf-8")),
            approval=json.loads(args.approval.read_text(encoding="utf-8")),
            prompt_key=args.prompt_key,
            expectations=[
                json.loads(p.read_text(encoding="utf-8"))
                for p in args.expectations_dir.glob("*.json")
            ],
            data_dir=args.data_dir,
            expectations_dir=args.expectations_dir,
            directory=args.output_dir,
            max_metric_calls=args.max_metric_calls,
            report_model=args.report_model,
            optimizer_model=args.optimizer_model,
        )
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
