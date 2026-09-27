"""Runtime options shared by generation and development evaluation."""

import argparse
import math
import os


def add_runtime_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--provider", choices=["openai", "openrouter"], default="openai"
    )
    parser.add_argument("--model", help="Model ID; required for OpenRouter")
    parser.add_argument(
        "--base-url", help="Official endpoint; defaults to the selected provider"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=90,
        help="Seconds per API attempt (default: 90)",
    )
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--max-output-tokens", type=int, default=20_000)
    parser.add_argument(
        "--reasoning-effort",
        choices=["none", "low", "medium", "high", "xhigh", "max"],
        default="medium",
        help="Model reasoning effort (default: medium)",
    )
    parser.add_argument(
        "--run-timeout",
        type=float,
        default=300,
        help="Seconds per client run (default: 300)",
    )
    parser.add_argument(
        "--max-calls",
        type=int,
        default=40,
        help="Model calls per client run (default: 40)",
    )
    parser.add_argument(
        "--publish-anyway",
        action="store_true",
        help="Save a source-rejected draft after repairs, marked for manual correction",
    )
    parser.add_argument(
        "--tone-of-voice", help="Override the config's shared prose style for this run"
    )
    parser.add_argument(
        "--adviser-confirmations",
        choices=["full", "discrepancy"],
        help="Adviser queries to display (default: config setting, or full); warnings and fee/tax checks remain",
    )


def provider_options(args, parser) -> dict:
    if args.provider == "openrouter" and not args.model:
        parser.error("--provider openrouter requires an explicit --model")
    if (
        not math.isfinite(args.run_timeout)
        or args.run_timeout <= 0
        or args.max_calls <= 0
    ):
        parser.error("--run-timeout and --max-calls must be positive and finite")
    if args.tone_of_voice is not None and not args.tone_of_voice.strip():
        parser.error("--tone-of-voice must not be blank")
    return {
        "provider": args.provider,
        "model": args.model or os.getenv("OPENAI_MODEL", "gpt-6-luna"),
        "base_url": args.base_url
        or ("https://openrouter.ai/api/v1" if args.provider == "openrouter" else None),
        "timeout": args.timeout,
        "max_retries": args.max_retries,
        "max_output_tokens": args.max_output_tokens,
        "reasoning_effort": args.reasoning_effort,
    }


def workflow_options(args) -> dict:
    return {
        "run_timeout": args.run_timeout,
        "max_calls": args.max_calls,
        "tone_of_voice": args.tone_of_voice,
        "adviser_confirmations": args.adviser_confirmations,
        "publish_anyway": args.publish_anyway,
    }
