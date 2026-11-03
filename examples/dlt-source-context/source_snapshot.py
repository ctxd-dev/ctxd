"""Load synthetic records with dlt, export loaded rows, then synchronize ctxd."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path

os.environ["RUNTIME__DLTHUB_TELEMETRY"] = "false"

import dlt

INITIAL = [
    {"id": 1, "name": "scheduler", "status": "ready"},
    {"id": 2, "name": "worker", "status": "ready"},
]
CHANGED = [
    {"id": 1, "name": "scheduler", "status": "ready"},
    {"id": 2, "name": "worker", "status": "maintenance"},
]


def command(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True)


def loaded_rows(pipeline):
    # The snapshot comes from the destination after loading, not the input list.
    with pipeline.sql_client() as client:
        with client.execute_query("SELECT id, name, status FROM services ORDER BY id") as cursor:
            return [dict(zip(("id", "name", "status"), row)) for row in cursor.fetchall()]


def export_snapshot(rows, destination):
    text = "# Service status\n\n" + "".join(
        f"- {row['id']}: {row['name']} = {row['status']}\n" for row in rows
    )
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(destination)
    return text


def sync_and_read():
    command(sys.executable, "-m", "ctxd.tracker_service", "sync-once")
    return command("ctxd", "files", "read", "DltExample/services.md", check=False)


def main():
    assert version("dlt") == "1.32.0"
    assert version("duckdb") == "1.5.6"
    assert version("ctxd") == "0.1.16"
    checkpoints = []
    with tempfile.TemporaryDirectory(prefix="dlt-source-example-") as directory:
        work = Path(directory)
        source = work / "source"
        source.mkdir()
        # This example is run in a disposable container with no host data mounts.
        os.chdir(work)
        os.environ["CTXD_CONFIG_PATH"] = str(work / "ctxd-config.json")
        os.environ["CTXD_BACKEND"] = "ctxfs"
        command("ctxd", "backend", "set", "ctxfs")
        command("ctxd", "folders", "add", str(source), "--name", "DltExample")
        pipeline = dlt.pipeline(
            pipeline_name="source_snapshot",
            pipelines_dir=str(work / "pipelines"),
            destination=dlt.destinations.duckdb(credentials=str(work / "loaded.duckdb")),
            dataset_name="service_catalog",
        )
        snapshot = source / "services.md"
        for stage, records in [("initial", INITIAL), ("changed", CHANGED)]:
            resource = dlt.resource(records, name="services", write_disposition="replace")
            info = pipeline.run(resource)
            assert not info.has_failed_jobs and info.loads_ids
            rows = loaded_rows(pipeline)
            assert rows == records
            text = export_snapshot(rows, snapshot)
            result = sync_and_read()
            assert result.returncode == 0 and result.stdout == text
            checkpoints.append({
                "stage": stage,
                "dlt_load_count": len(info.loads_ids),
                "dlt_failed_jobs": info.has_failed_jobs,
                "loaded_rows": rows,
                "snapshot_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "ctxd_read": result.stdout,
                "passed": True,
            })

        # Retire the exported source. This does not delete the DuckDB table.
        snapshot.unlink()
        result = sync_and_read()
        assert result.returncode != 0
        assert result.stderr.strip() == "ctxfs path was not found"
        assert loaded_rows(pipeline) == CHANGED
        checkpoints.append({
            "stage": "retired_snapshot",
            "ctxd_exit_code": result.returncode,
            "ctxd_error": result.stderr.strip(),
            "duckdb_rows_retained": len(CHANGED),
            "passed": True,
        })
    print("CTXD_DLT_RESULT=" + json.dumps({
        "versions": {name: version(name) for name in ("dlt", "duckdb", "ctxd")},
        "checkpoints": checkpoints,
        "source_format": "UTF-8 Markdown exported from loaded DuckDB rows",
        "model_calls": 0,
        "passed": True,
    }))


if __name__ == "__main__":
    main()
