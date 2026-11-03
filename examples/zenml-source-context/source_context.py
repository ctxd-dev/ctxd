"""A local ZenML pipeline: snapshot artifact, explicit sync, verified read."""

import hashlib
import json
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

from zenml import pipeline, step
from zenml.client import Client


@step(enable_cache=True)
def source_snapshot(content: str | None) -> dict:
    # This value is determined by the input parameter, so it can be cached.
    return {"content": content}


@step(enable_cache=False)
def synchronize_and_read(snapshot: dict) -> dict:
    # These side effects must run even when the snapshot artifact is cached.
    source = Path("/tmp/ctxd-zenml-source/runbook.md")
    content = snapshot["content"]
    if content is None:
        source.unlink(missing_ok=True)
    else:
        temporary = source.with_suffix(".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(source)

    subprocess.run(
        [sys.executable, "-m", "ctxd.tracker_service", "sync-once"],
        check=True, capture_output=True, text=True,
    )
    result = subprocess.run(
        ["ctxd", "files", "read", "ZenMLExample/runbook.md"],
        capture_output=True, text=True,
    )
    if content is None:
        if result.returncode == 0 or result.stderr.strip() != "ctxfs path was not found":
            raise RuntimeError("Expected the removed source to be absent from ctxd")
        return {"state": "removed"}
    if result.returncode != 0 or result.stdout != content:
        raise RuntimeError("ctxd read does not match the requested snapshot")
    return {"state": "readable", "sha256": hashlib.sha256(content.encode()).hexdigest()}


@pipeline(enable_cache=True)
def refresh_context(content: str | None) -> None:
    snapshot = source_snapshot(content=content)
    synchronize_and_read(snapshot=snapshot)


if __name__ == "__main__":
    snapshots = [
        "Run unit tests before release.\n",
        "Run unit tests before release.\n",
        "Run unit and integration tests before release.\n",
        None,
        None,
    ]
    runs = []
    for index, content in enumerate(snapshots):
        submitted = refresh_context(content=content)
        run = Client().get_pipeline_run(submitted.id)
        states = {name: step_run.status.value for name, step_run in run.steps.items()}
        expected = {
            "source_snapshot": "cached" if index in (1, 4) else "completed",
            "synchronize_and_read": "completed",
        }
        if run.status.value != "completed" or states != expected:
            raise RuntimeError(f"Unexpected pipeline or caching result: {run.status}, {states}")
        output = run.steps["synchronize_and_read"].output.load()
        expected_output = (
            {"state": "removed"} if content is None else
            {"state": "readable", "sha256": hashlib.sha256(content.encode()).hexdigest()}
        )
        if output != expected_output:
            raise RuntimeError("Stored output artifact does not match the expected result")
        runs.append({"operation": "remove" if content is None else "upsert", "steps": states, "output": output})
    print("CTXD_ZENML_RESULT=" + json.dumps({
        "zenml": version("zenml"),
        "ctxd": version("ctxd"),
        "runs": runs,
        "passed": True,
        "model_calls": 0,
    }))
