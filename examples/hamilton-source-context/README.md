# A Hamilton graph for changing source context

This example uses three Hamilton nodes to write a generated artifact, synchronize
it into ctxd, and read the resulting source. ctxd provides context infrastructure
between agents and data. Hamilton owns the dependency order in this example.

| Node | Work | Depends on |
| --- | --- | --- |
| `source_artifact` | Create, edit or remove a synthetic `instructions.md` | Desired text and presence |
| `synchronized_artifact` | Run `ctxd.tracker_service sync-once` | `source_artifact` |
| `context_read` | Discover the source path and verify its content or absence | `synchronized_artifact` and the last known path |

The graph runs six times, sequentially: write, repeat the same write, edit,
repeat the edit, remove, and repeat the removal. Each run executes the whole
chain. There is no cache adapter or background tracker; caching these nodes
would require a separate policy for their side effects.

## Run

From any Linux workstation, as a user with Docker access, in the directory
containing `Dockerfile` and `run.py`:

```sh
docker build -t ctxd-hamilton-example .
docker run --rm --init --network none --cap-drop ALL --security-opt no-new-privileges ctxd-hamilton-example
```

The build downloads Apache Hamilton 1.90.0 and uses a digest-pinned image
containing ctxd 0.1.16 and Python 3.12.16. The run creates its own temporary data
inside the container, has no host mounts, and needs no model or external service.
The container runs as its unprivileged `ctxd` user.

Successful output is JSON with `"passed": true` and six results. The checks
compare the read text and SHA-256 content hash after writes, and require a 404
read plus an empty glob after removal. Repeating the same desired state leaves
the source unchanged. The source path is discovered from ctxfs, because it
contains a generated folder identifier.

`sync-once` is followed by a bounded read check that allows up to 20 seconds for
the expected state to be visible. The fixture tests these local operations; it
does not measure synchronization latency or distributed execution. Run each
experiment in a fresh container so its registered folders and source data are
isolated.

## Adapt

Replace the synthetic artifact generation in `source_artifact` with the output
of your Hamilton pipeline. Keep the synchronization node dependent on the
completed artifact, and make consumers depend on synchronization. If the
pipeline removes an artifact, preserve the last known source path so that its
removal can be checked too.

The example follows Hamilton's [typed node dependencies](https://hamilton.apache.org/concepts/node/)
and [Driver execution model](https://hamilton.apache.org/concepts/driver/).
See the [Hamilton installation guide](https://hamilton.apache.org/get-started/install/)
and [ctxd source](https://github.com/ctxd-dev/ctxd) for the underlying packages.
