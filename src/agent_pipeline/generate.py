"""Command-line composition for the shared, source-backed report workflow."""

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from agent_pipeline.contracts import Err
from agent_pipeline.providers import create_provider
from agent_pipeline.workflow import run_generation


class _UnavailableProvider:
    def __init__(self, error):
        self.error = error

    def complete(self, **kwargs):
        return Err(self.error)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a source-backed advice report draft."
    )
    parser.add_argument("--client", required=True, help="folder name under data/")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--config", type=Path, default=Path("config/template_config.json")
    )
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument(
        "--provider", default="openai", choices=["openai", "openrouter"]
    )
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--cache-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.provider == "openrouter" and not args.model:
        parser.error("--provider openrouter requires an explicit --model")
    if (
        Path(args.client).name != args.client
        or args.client in {".", ".."}
        or "/" in args.client
        or "\\" in args.client
    ):
        parser.error("--client must name one folder under --data-dir")
    load_dotenv()
    selected = create_provider(
        provider=args.provider,
        model=args.model or os.getenv("OPENAI_MODEL", "gpt-6-luna"),
        base_url=args.base_url,
        cache_dir=args.cache_dir,
    )
    provider = (
        _UnavailableProvider(selected.error)
        if isinstance(selected, Err)
        else selected.value
    )
    result = run_generation(
        client_dir=args.data_dir / args.client,
        config_path=args.config,
        output_dir=args.output_dir,
        provider=provider,
        cache_dir=args.cache_dir,
    )
    if isinstance(result, Err):
        print(f"{result.error.stage}: {result.error.code} — {result.error.message}")
        return 1
    print(f"Draft needs adviser review: {result.value['output_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
