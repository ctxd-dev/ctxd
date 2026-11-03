"""Run a local Dagster asset example against the released ctxd package."""

import hashlib
import json
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import dagster as dg


class Snapshot(dg.Config):
    content: str | None


@dg.asset
def source_snapshot(config: Snapshot) -> dict:
    # Pass the small value itself, rather than a worker-local file path.
    return {"content": config.content}


@dg.asset(
    check_specs=[dg.AssetCheckSpec(name="matches_snapshot", asset="source_context")],
)
def source_context(source_snapshot: dict):
    source = Path("/tmp/ctxd-dagster-source/runbook.md")
    content = source_snapshot["content"]
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
        ["ctxd", "files", "read", "DagsterExample/runbook.md"],
        capture_output=True, text=True,
    )
    if content is None:
        matches = (
            result.returncode != 0
            and result.stderr.strip() == "ctxfs path was not found"
        )
        metadata = {"state": "removed"}
    else:
        matches = result.returncode == 0 and result.stdout == content
        metadata = {
            "state": "readable",
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
        }

    yield dg.AssetCheckResult(passed=matches, check_name="matches_snapshot")
    if not matches:
        # A failed read must also fail the run, not just record a failed check.
        raise dg.Failure("ctxd read does not match the requested source state")
    yield dg.MaterializeResult(metadata=metadata)


if __name__ == "__main__":
    snapshots = [
        "Run unit tests before release.\n",
        "Run unit tests before release.\n",
        "Run unit and integration tests before release.\n",
        None,
        None,
    ]
    runs = []
    for content in snapshots:
        result = dg.materialize_to_memory(
            [source_snapshot, source_context],
            run_config=dg.RunConfig(ops={"source_snapshot": Snapshot(content=content)}),
        )
        if not result.success:
            raise RuntimeError("Asset run failed")
        materialized = [
            event.asset_key.to_user_string()
            for event in result.all_events
            if event.event_type_value == "ASSET_MATERIALIZATION"
        ]
        checks = [
            event.event_specific_data
            for event in result.all_events
            if event.event_type_value == "ASSET_CHECK_EVALUATION"
        ]
        if materialized != ["source_snapshot", "source_context"]:
            raise RuntimeError(f"Unexpected asset order: {materialized}")
        if (
            len(checks) != 1
            or not checks[0].passed
            or checks[0].check_name != "matches_snapshot"
            or checks[0].asset_key.to_user_string() != "source_context"
        ):
            raise RuntimeError("Expected one successful native asset check")
        runs.append({
            "operation": "remove" if content is None else "upsert",
            "materialized": materialized,
            "check": {"name": checks[0].check_name, "passed": checks[0].passed},
        })
    print("CTXD_DAGSTER_RESULT=" + json.dumps({
        "dagster": version("dagster"),
        "ctxd": version("ctxd"),
        "runs": runs,
        "passed": True,
        "model_calls": 0,
    }))
