"""The aggregate gate must never hide missing or unsuccessful checks."""

import subprocess
from pathlib import Path

import pytest
from scripts.verify import verify


def runner(
    *,
    failed_command: tuple[str, ...] = (),
    report: str | None = None,
    calls: list[list[str]] | None = None,
):
    def run(command: list[str], *, cwd: Path, check: bool):
        assert cwd.is_dir()
        assert check is False
        if calls is not None:
            calls.append(command[2:])
        if command[2] == "pytest" and report is not None:
            output = Path(command[command.index("--junitxml") + 1])
            output.write_text(report, encoding="utf-8")
        return subprocess.CompletedProcess(
            command,
            1
            if failed_command
            and tuple(command[2 : 2 + len(failed_command)]) == failed_command
            else 0,
        )

    return run


PASS_REPORT = '<testsuites><testsuite><testcase name="ok"/></testsuite></testsuites>'


def test_all_successful_checks_pass():
    calls = []
    assert verify(run=runner(report=PASS_REPORT, calls=calls)) == 0
    assert calls[:3] == [
        ["ruff", "check", "."],
        ["ruff", "format", "--check", "."],
        ["ty", "check", "."],
    ]
    assert len(calls) == 4
    assert calls[3][:2] == ["pytest", "--junitxml"]


@pytest.mark.parametrize(
    "command", [("ruff", "check"), ("ruff", "format"), ("ty",), ("pytest",)]
)
def test_failed_check_fails_aggregate(command):
    assert verify(run=runner(failed_command=command, report=PASS_REPORT)) != 0


@pytest.mark.parametrize(
    "report",
    [
        None,
        "not XML",
        "<testsuites/>",
        "<testsuites><testsuite><testcase><skipped/></testcase></testsuite></testsuites>",
        "<testsuites><testsuite><testcase><failure/></testcase></testsuite></testsuites>",
    ],
)
def test_missing_empty_skipped_or_failed_test_evidence_fails(report):
    assert verify(run=runner(report=report)) != 0


def test_missing_tool_fails_cleanly():
    def unavailable(command: list[str], *, cwd: Path, check: bool):
        raise FileNotFoundError("tool is missing")

    assert verify(run=unavailable) != 0
