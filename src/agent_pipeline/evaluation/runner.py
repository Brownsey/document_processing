"""Independent, source-reviewed scoring of rendered reports.

Expectations are deliberately imported only by this module, never by generation.
The deterministic checks are a hard floor, not a substitute for human prose review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from agent_pipeline.contracts import Err, InputError, Ok
from agent_pipeline.evaluation.scoring import score_report


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()


def fingerprint_files(paths: list[Path], *, root: Path | None = None) -> str:
    return fingerprint(
        [
            (
                p.relative_to(root).as_posix() if root else str(p),
                hashlib.sha256(p.read_bytes()).hexdigest(),
            )
            for p in sorted(paths)
        ]
    )


def summarise_runs(runs: list[dict]) -> dict:
    usage = [record for run in runs for record in run.get("usage", [])]
    valid = sum(bool(r.get("passed")) for r in runs)
    return {
        "runs": len(runs),
        "valid_drafts": valid,
        "input_tokens": sum(
            u.get("usage", u).get("input_tokens", 0) or 0 for u in usage
        ),
        "output_tokens": sum(
            u.get("usage", u).get("output_tokens", 0) or 0 for u in usage
        ),
        "retries": sum(bool(u.get("attempt", 1) > 1) for u in usage),
        "latency_seconds": sum(r.get("latency_seconds", 0) for r in runs),
        "pass_rate": valid / len(runs) if runs else None,
    }


def _read_inputs(client_dir: Path):
    from agent_pipeline.adapters.evidence import (
        MAX_SOURCE_BYTES,
        MAX_TOTAL_BYTES,
        _inventory_paths,
        _source_bytes,
    )

    inventory = _inventory_paths(client_dir)
    captured: dict[str, bytes] = {}
    if isinstance(inventory, Err):
        return inventory
    try:
        root = client_dir.resolve(strict=True)
        for source in inventory.value:
            raw = _source_bytes(source, root)
            if (
                len(raw) > MAX_SOURCE_BYTES
                or sum(map(len, captured.values())) + len(raw) > MAX_TOTAL_BYTES
            ):
                raise ValueError("Source exceeded bounds")
            captured[source.relative_to(root).as_posix()] = raw
    except (OSError, ValueError):
        return Err(
            InputError(
                "unsafe_source_path",
                "Client source could not be snapshotted safely.",
                stage="evidence",
            )
        )
    return Ok(captured)


def evaluate_case(
    *,
    client_dir: Path,
    config_path: Path,
    expected_path: Path,
    output_dir: Path,
    provider: Any,
    run_timeout: float = 300,
    max_calls: int = 40,
    tone_of_voice: str | None = None,
    adviser_confirmations: str | None = None,
    publish_anyway: bool = False,
) -> dict:
    from agent_pipeline.generate import run_generation

    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if tone_of_voice is not None:
        config["tone_of_voice"] = tone_of_voice
    if adviser_confirmations is not None:
        config["adviser_confirmations"] = adviser_confirmations
    evaluation_id = uuid.uuid4().hex
    output_dir = output_dir / evaluation_id
    if output_dir.resolve().is_relative_to(client_dir.resolve()):
        raise ValueError("Evaluation outputs must be outside the source directory.")
    inputs = _read_inputs(client_dir)
    input_error = inputs if isinstance(inputs, Err) else None
    captured = inputs.value if isinstance(inputs, Ok) else {}
    package_root = Path(__file__).resolve().parents[1]
    source_files = sorted(package_root.rglob("*.py"))
    fingerprints = {
        "inputs": fingerprint(
            [
                (name, hashlib.sha256(raw).hexdigest())
                for name, raw in sorted(captured.items())
            ]
        ),
        "expectations": fingerprint(expected),
        "config": fingerprint(config),
        "scorer": fingerprint_files(
            list(Path(__file__).parent.rglob("*.py")), root=Path(__file__).parent
        ),
        "code": fingerprint_files(source_files, root=package_root),
        "publish_anyway": fingerprint(publish_anyway),
        "model_settings": fingerprint(getattr(provider, "settings", {})),
        "execution_limits": fingerprint(
            {"timeout": run_timeout, "max_calls": max_calls}
        ),
    }
    snapshot = output_dir / "snapshot"
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "expectations.json").write_text(
        json.dumps(expected, indent=2), encoding="utf-8"
    )
    (snapshot / "config.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8"
    )
    source_snapshot = snapshot / "inputs" / client_dir.name
    for name, raw in captured.items():
        destination = source_snapshot / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    (snapshot / "code").mkdir(exist_ok=True)
    for source in source_files:
        destination = snapshot / "code" / source.relative_to(package_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    started = time.monotonic()
    result = input_error or run_generation(
        client_dir=source_snapshot,
        config_path=snapshot / "config.json",
        output_dir=output_dir,
        provider=provider,
        run_timeout=run_timeout,
        max_calls=max_calls,
        publish_anyway=publish_anyway,
    )
    elapsed = time.monotonic() - started
    if isinstance(result, Err):
        outcome = {
            "passed": False,
            "score": 0,
            "failures": [result.error.code],
            "error": {
                "type": type(result.error).__name__,
                "code": result.error.code,
                "stage": result.error.stage,
            },
            "usage": list(
                getattr(provider, "usage_records", getattr(provider, "records", []))
            ),
        }
    else:
        manifest = result.value
        report = Path(manifest["output_path"]).read_text(encoding="utf-8")
        outcome = score_report(report, manifest, expected, config)
        outcome.update({"manifest": manifest, "usage": manifest.get("usage", [])})
    outcome.update(
        {
            "evaluation_id": evaluation_id,
            "client": client_dir.name,
            "latency_seconds": elapsed,
            "fingerprints": fingerprints,
            "input_snapshot": str(snapshot.resolve()),
            "source_snapshot": str(source_snapshot.resolve()),
        }
    )
    (output_dir / f"evaluation-{outcome['evaluation_id']}.json").write_text(
        json.dumps(outcome, indent=2, default=str), encoding="utf-8"
    )
    return outcome


def main(argv: list[str] | None = None) -> int:
    from agent_pipeline.cli import (
        add_runtime_arguments,
        provider_options,
        workflow_options,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path("config/template_config.json")
    )
    parser.add_argument("--baseline-config", type=Path)
    parser.add_argument(
        "--clients",
        nargs="+",
        default=[
            "client_01_clean",
            "client_02_medium",
            "client_03_hard",
            "client_04_stretch",
        ],
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--expectations-dir", type=Path, default=Path("eval/development")
    )
    parser.add_argument("--output-dir", type=Path, default=Path(".local/evaluations"))
    parser.add_argument("--mode", choices=["evaluate", "compare"], default="evaluate")
    add_runtime_arguments(parser)
    parser.add_argument("--repetitions", type=int, default=1)
    args = parser.parse_args(argv)
    from dotenv import load_dotenv

    load_dotenv()
    settings = provider_options(args, parser)
    if (
        not 1 <= args.repetitions <= 2
        or args.mode == "compare"
        and not args.baseline_config
    ):
        parser.error("Use 1–2 repetitions; compare requires --baseline-config.")
    from agent_pipeline.adapters.providers import create_provider

    if any(
        Path(client).name != client
        or client in {".", ".."}
        or "/" in client
        or "\\" in client
        for client in args.clients
    ):
        parser.error("Client must be a directory name, not a path.")
    root = args.output_dir / uuid.uuid4().hex
    runs = []
    configs = [("candidate", args.config)]
    if args.mode == "compare":
        configs.insert(0, ("baseline", args.baseline_config))
    stopping_reason = None
    for label, config_path in configs:
        for client in args.clients:
            for repeat in range(args.repetitions):
                provider = create_provider(**settings)
                if isinstance(provider, Err):
                    result: dict[str, Any] = {
                        "passed": False,
                        "score": 0,
                        "client": client,
                        "failures": [provider.error.code],
                        "error": {
                            "type": type(provider.error).__name__,
                            "code": provider.error.code,
                            "stage": provider.error.stage,
                        },
                        "usage": [],
                    }
                    stopping_reason = provider.error.code
                else:
                    result = evaluate_case(
                        client_dir=args.data_dir / client,
                        config_path=config_path,
                        expected_path=args.expectations_dir / f"{client}.json",
                        output_dir=root / label / client / str(repeat + 1),
                        provider=provider.value,
                        **workflow_options(args),
                    )
                result["variant"] = label
                runs.append(result)
                if result.get("error", {}).get("code") in {
                    "authentication",
                    "permission",
                    "model_unavailable",
                    "invalid_request",
                    "missing_credentials",
                }:
                    stopping_reason = result["error"]["code"]
                if stopping_reason:
                    break
            if stopping_reason:
                break
        if stopping_reason:
            break
    root.mkdir(parents=True, exist_ok=True)
    summary = {
        "variants": {
            label: summarise_runs([r for r in runs if r["variant"] == label])
            for label, _ in configs
        },
        "runs": runs,
        "review_status": "PENDING USER REVIEW",
        "stopping_reason": stopping_reason,
        "requested_runs": len(configs) * len(args.clients) * args.repetitions,
    }
    path = root / "comparison.json"
    path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(str(path))
    return 0 if all(r["passed"] for r in runs) else 1
