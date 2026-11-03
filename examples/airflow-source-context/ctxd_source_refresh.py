"""A local Airflow example: materialize, synchronize, then read source context."""

from datetime import timedelta

from airflow.sdk import Param, dag, get_current_context, task


@dag(
    dag_id="ctxd_source_refresh",
    schedule=None,
    catchup=False,
    max_active_runs=1,
    params={"content": Param("Run unit tests before release.\n", type=["string", "null"])},
    tags=["example", "ctxd"],
)
def ctxd_source_refresh():
    @task
    def source_snapshot():
        # A small immutable value crosses the task boundary, not a local path.
        return {"content": get_current_context()["params"]["content"]}

    @task(retries=1, retry_delay=timedelta(seconds=1))
    def synchronize_and_read(snapshot):
        import hashlib
        import subprocess
        import sys
        from pathlib import Path

        source = Path("/tmp/ctxd-airflow-source/runbook.md")
        content = snapshot["content"]
        if content is None:
            source.unlink(missing_ok=True)
        else:
            # Atomic replacement keeps a partial write out of the source path.
            temporary = source.with_suffix(".tmp")
            temporary.write_text(content)
            temporary.replace(source)

        subprocess.run(
            [sys.executable, "-m", "ctxd.tracker_service", "sync-once"],
            check=True,
            capture_output=True,
            text=True,
        )
        result = subprocess.run(
            ["ctxd", "files", "read", "AirflowExample/runbook.md"],
            capture_output=True,
            text=True,
        )
        if content is None:
            if result.returncode == 0 or result.stderr.strip() != "ctxfs path was not found":
                raise RuntimeError("Removed source is still readable, or the read failed unexpectedly")
            return {"state": "removed"}
        if result.returncode or result.stdout != content:
            raise RuntimeError("Synchronized context does not match the requested snapshot")
        return {"state": "readable", "sha256": hashlib.sha256(content.encode()).hexdigest()}

    synchronize_and_read(source_snapshot())


dag = ctxd_source_refresh()


if __name__ == "__main__":
    import json
    from importlib.metadata import version

    # dag.test() runs actual task instances with a local metadata database.
    snapshots = [
        "Run unit tests before release.\n",
        "Run unit tests before release.\n",
        "Run unit and integration tests before release.\n",
        None,
        None,
    ]
    results = []
    for content in snapshots:
        run = dag.test(run_conf={"content": content})
        if run.state != "success":
            raise RuntimeError(f"DAG run failed: {run.state}")
        states = {ti.task_id: str(ti.state) for ti in run.get_task_instances()}
        if set(states) != {"source_snapshot", "synchronize_and_read"} or set(states.values()) != {"success"}:
            raise RuntimeError(f"Unexpected task states: {states}")
        results.append({"operation": "remove" if content is None else "upsert", "tasks": states})
    print("CTXD_AIRFLOW_RESULT=" + json.dumps({
        "airflow": version("apache-airflow"),
        "ctxd": version("ctxd"),
        "runs": results,
        "passed": True,
        "model_calls": 0,
    }))
