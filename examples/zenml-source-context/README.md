# Synchronize context on every ZenML run

A cached artifact can preserve a snapshot. Synchronizing external state still needs to execute. This example makes that boundary explicit in a two-step ZenML pipeline.

`source_snapshot` stores a small, fixed value as an artifact and allows caching. `synchronize_and_read` uses `@step(enable_cache=False)`: it writes or removes the source file, synchronizes ctxd, and checks what the context reader returns. A mismatch fails the step.

## Run the example

From any Linux workstation as a user with Docker access, in the directory containing these files:

```bash
docker build -t ctxd-zenml-example .
docker run --rm --init --network none ctxd-zenml-example
```

The build installs `zenml[local]==0.98.0` and `ctxd==0.1.16` from PyPI. The run uses ZenML's local stack and synthetic data inside the container. It has no host mounts, external network, model calls or API keys. ZenML analytics is disabled; no server or browser is started.

- [Pipeline and executable checks](source_context.py)
- [Pinned Docker build](Dockerfile)
- [Container setup](run-example.sh)
- [Validation evidence](validation.json)

Five real pipeline runs cover initial content, the same snapshot again, changed content, removal, and repeated removal. The runner reads actual step states and loads the result artifacts. It requires two snapshot cache hits and a completed synchronization step in every run. The final `CTXD_ZENML_RESULT` line reports the outcomes.

## Caching and local state

- Only the fixed snapshot step is cached. ZenML's cache does not automatically notice filesystem changes or repeat external side effects.
- The consuming step keeps the write, explicit synchronization and read together. Atomic replacement avoids partially written source content; removing an absent file succeeds.
- Removal requires ctxd's specific missing-path response. A connection failure does not count as successful deletion.
- The runner executes sequentially. Repeated desired states were tested; crash recovery, automatic retries and concurrent writers were not. Replaying an older snapshot can overwrite newer content.

ZenML's artifacts and metadata are separate from ctxd's source folder, configuration, Unix socket and context store. All remain on one host under one operating-system user for these five runs. The container removes them on exit. Passing a snapshot artifact does not distribute ctxd's service or state, and a shared source folder alone is insufficient. Distributed workers need a storage and service design plus coordination between writers.

This is a local executable example. It does not configure a schedule, train a model or establish a maintained ZenML integration.

[Caching rules](https://docs.zenml.io/user-guides/starter-guide/cache-previous-executions) · [Local installation](https://docs.zenml.io/getting-started/installation) · [Local orchestrator](https://docs.zenml.io/stacks/stack-components/orchestrators/local.md) · [Local artifact store](https://docs.zenml.io/stacks/stack-components/artifact-stores/local.md)
