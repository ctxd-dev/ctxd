# Refresh local agent context from an Airflow task

Keep the source write, synchronization and read check inside one task. Passing a local filename through XCom does not make that file available on another worker.

This example uses Apache Airflow 3.3.2 and ctxd 0.1.16. `source_snapshot` passes a small, fixed value through XCom. `synchronize_and_read` writes or removes the source, runs ctxd synchronization, and checks the resulting read before succeeding. All filesystem and ctxd work happens in that consuming task.

## Run the example

From any Linux workstation as a user with Docker access, in the directory containing these files:

```bash
docker build -t ctxd-airflow-example .
docker run --rm --init --network none ctxd-airflow-example
```

The build downloads the pinned Airflow image and PyPI packages. The run uses synthetic data inside the container, with no host mounts, external network access, model calls or API keys. It initializes a local Airflow metadata database and runs the DAG with `dag.test()`.

Files:

- [DAG and executable checks](ctxd_source_refresh.py)
- [Pinned Docker build](Dockerfile)
- [Container setup](run-example.sh)

Five actual DAG runs check the initial source, the same snapshot again, changed content, removal, and repeated removal. Both tasks must succeed in every run. The final `CTXD_AIRFLOW_RESULT` line reports the result. [Validation evidence](validation.json) records the tested versions and outcomes.

## Task boundaries and retries

- The snapshot is fixed for a run. A retry applies the same desired content, rather than reading a changing upstream value.
- File replacement is atomic. Removing an absent file succeeds. Explicit synchronization finishes before the read check.
- A read failure or content mismatch fails the task. A removal succeeds only when the read reports that the source is absent, not when an arbitrary connection error occurs.
- `max_active_runs=1` serializes this DAG's runs. It does not coordinate other writers or protect a newer snapshot from an intentionally replayed older run.
- Repeat upsert and removal were tested. Automatic retry after an injected failure was not exercised.

The runner uses one local host and operating-system user. Across runs, the source folder, ctxd configuration, Unix socket and context store must remain available to that user. A shared source directory alone does not share the ctxd service or its configuration. This example does not establish a distributed Celery/Kubernetes deployment. Large production snapshots also need durable external storage rather than the small XCom value used here.

[Airflow task best practices](https://airflow.apache.org/docs/apache-airflow/3.3.2/best-practices.html) · [Real DAG testing](https://airflow.apache.org/docs/apache-airflow/3.3.2/core-concepts/debug.html) · [Extending the official image](https://airflow.apache.org/docs/docker-stack/build.html)
