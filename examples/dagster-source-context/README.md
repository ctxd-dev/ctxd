# Check synchronized context as a Dagster asset

Writing a source file is one step. Checking what the context reader can retrieve is another. This example connects them in a Dagster asset and records a native asset check.

`source_snapshot` supplies a small, fixed value. Its dependent asset, `source_context`, replaces or removes the source file, runs explicit ctxd synchronization, and checks the resulting read. It records `matches_snapshot` and fails the run if the read does not match.

## Run the example

From any Linux workstation as a user with Docker access, in the directory containing these files:

```bash
docker build -t ctxd-dagster-example .
docker run --rm --init --network none ctxd-dagster-example
```

The build installs Dagster 1.13.28 and ctxd 0.1.16 from PyPI. The run uses synthetic data inside the container, with no host mounts, external network, model calls or API keys. Dagster telemetry is disabled.

- [Assets and executable checks](source_context.py)
- [Pinned Docker build](Dockerfile)
- [Container setup](run-example.sh)
- [Validation evidence](validation.json)

Five real `materialize_to_memory()` runs exercise initial content, the same content again, changed content, removal, and repeated removal. Each run must materialize both assets in dependency order and report one passing `matches_snapshot` check. The final `CTXD_DAGSTER_RESULT` line reports the outcomes.

## State and execution

- The small snapshot value is passed between assets in memory. A local path would not transfer a file to another worker.
- The consuming asset keeps the write, explicit synchronization and read in the same process environment. File replacement is atomic; removing an absent file succeeds.
- Removal requires ctxd's specific missing-path response. A service or connection failure does not count as successful deletion.
- Runs execute sequentially in this example. Repeating a desired state was tested; retry scheduling and failure recovery were not. Replaying an older snapshot can overwrite newer content.

The source folder, ctxd configuration, Unix socket and context store belong to one host and operating-system user. They persist across the five runs inside the container, then disappear when the container exits. Sharing only the source directory does not share ctxd's service or state. A distributed deployment needs an explicit storage and service design, durable snapshot transport, and coordination between writers.

This is a local executable example. It does not set up a scheduler, a production deployment or a maintained Dagster integration.

[Asset definitions](https://docs.dagster.io/guides/build/assets/defining-assets) · [In-process asset execution](https://docs.dagster.io/api/dagster/execution) · [Native asset checks](https://docs.dagster.io/api/dagster/asset-checks)
