# Refresh source context in a Prefect task

The task writes a desired source snapshot, synchronizes ctxd, and checks the resulting read before succeeding. It sets `persist_result=False` explicitly: this task changes external state, so a cached return value must not replace its work.

## Run

On a Linux AMD64 workstation as a user with Docker access, from this directory:

```bash
docker build -t ctxd-prefect-example .
docker run --rm --init --network none ctxd-prefect-example
```

The build installs Prefect 3.9.1 and ctxd 0.1.16 from PyPI. The run starts a temporary Prefect server inside the container. It uses synthetic source data, no host mounts, no external runtime network, and no model requests or API keys. The container and its data are removed when it finishes.

[refresh_context.py](refresh_context.py) contains a native Prefect task and flow. A direct task call waits for its result. Five sequential flow runs check initial content, repeated content after an external source edit, changed content, removal, and repeated removal. Each flow must complete, and each read must match the requested snapshot. Removal accepts a missing-source response, not any arbitrary read error. [validation.json](validation.json) records the actual outcomes.

## Execution boundary

The write, explicit synchronization and read check run on the same local host and operating-system user. The task atomically replaces the source file. Retrying a fixed snapshot reapplies the desired state; repeated upsert and removal were tested, but failure-triggered retries were not injected.

The runner calls flows sequentially. It does not coordinate concurrent writers or prevent an older snapshot from replacing newer content. A remote-worker deployment needs an explicit design for the source, ctxd configuration, service socket and context store. This example does not establish that deployment.

Result persistence is disabled on this task and flow. This controls Prefect result caching; it does not lock the source or make the filesystem and context store one transaction. Prefect's run metadata remains in its temporary local server until the container exits.

[Prefect task execution](https://github.com/PrefectHQ/prefect/blob/3.9.1/docs/v3/concepts/tasks.mdx) · [Caching and result persistence](https://github.com/PrefectHQ/prefect/blob/3.9.1/docs/v3/concepts/caching.mdx) · [ctxd source](https://github.com/ctxd-dev/ctxd)
