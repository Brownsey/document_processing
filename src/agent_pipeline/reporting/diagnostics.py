"""Atomic output writes, fingerprints and repair diagnostics."""

import hashlib
import json
import os
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from agent_pipeline.contracts import RunState


def hash_value(value) -> str:
    if not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True, default=str).encode()
    return hashlib.sha256(value).hexdigest()


def run_fingerprints(config: dict, settings: dict) -> dict:
    return {
        "config": hash_value(config),
        "model_settings": hash_value(settings),
        "prompts": hash_value(
            {
                k: v
                for k, v in config.items()
                if k.endswith("prompt") or k in {"global_instructions", "tone_of_voice"}
            }
            | {"slots": [s.get("placeholders", {}) for s in config["sections"]]}
        ),
        "code": hash_value(
            {
                str(p.relative_to(Path(__file__).parents[2])): hash_value(
                    p.read_bytes()
                )
                for p in sorted(Path(__file__).parents[2].rglob("*.py"))
            }
        ),
    }


def atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def usage_records(provider) -> list[dict]:
    return list(getattr(provider, "usage_records", getattr(provider, "records", [])))


def record_repair(state: RunState, kind: str, trigger: dict, rejected_text: str = ""):
    manifest = state.manifest
    manifest["repair_counts"][kind] += 1
    instant = datetime.now(timezone.utc)
    event = {
        "kind": kind,
        "attempt": manifest["repair_counts"][kind],
        "timestamp": instant.isoformat(),
        "trigger": trigger,
    }
    manifest["repair_history"].append(event)
    details = {"client": manifest["client"], "run_id": manifest["run_id"], **event}
    if kind == "facts":
        details["facts"] = state.facts
    content = (
        "# Repair diagnostic — not an approved report\n\n"
        "## Trigger\n\n```json\n"
        + json.dumps(details, indent=2, ensure_ascii=False, default=str)
        + "\n```\n"
    )
    if rejected_text:
        content += "\n## Rejected content\n\n" + rejected_text + "\n"
    reserved = False
    try:
        while True:
            path = Path(manifest["output_path"]).with_name(
                f"{manifest['client']}_repair_{instant:%Y%m%dT%H%M%S%fZ}.md"
            )
            try:
                path.touch(exist_ok=False)
                reserved = True
                break
            except FileExistsError:
                instant += timedelta(microseconds=1)
        atomic_write(path, content)
        event["diagnostic_path"] = str(path)
    except OSError:
        if reserved:
            with suppress(OSError):
                path.unlink()
        manifest["diagnostics"].append(
            {"code": "repair_diagnostic_write_failed", "path": str(path)}
        )
