"""Run the complete offline quality gate with the current locked environment."""

import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path


def verify(*, run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> int:
    """Fail if a tool fails, evidence is missing, or any test is skipped."""
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="document-processing-verify-") as directory:
        report = Path(directory) / "pytest.xml"
        commands = (
            ["ruff", "check", "."],
            ["ruff", "format", "--check", "."],
            ["ty", "check", "."],
            ["pytest", "--junitxml", str(report)],
        )
        failed = False
        for arguments in commands:
            print(f"\nRunning {' '.join(arguments)}", flush=True)
            try:
                result = run([sys.executable, "-m", *arguments], cwd=root, check=False)
                failed |= result.returncode != 0
            except OSError as error:
                print(f"Unable to run required check: {error}", file=sys.stderr)
                failed = True

        try:
            cases = list(ET.parse(report).getroot().iter("testcase"))
            if not cases or any(
                case.find(outcome) is not None
                for case in cases
                for outcome in ("skipped", "failure", "error")
            ):
                print("Tests must run and pass without skips.", file=sys.stderr)
                failed = True
        except (OSError, ET.ParseError) as error:
            print(f"Missing or invalid test evidence: {error}", file=sys.stderr)
            failed = True
        return int(failed)


if __name__ == "__main__":
    raise SystemExit(verify())
