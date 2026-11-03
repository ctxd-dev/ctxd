# Trace changing source context with Phoenix

This example sends real ctxd Model Context Protocol (MCP) tool calls to a local
Phoenix server as OpenInference spans. It checks three stages using synthetic
source content:

1. Read the original source.
2. Edit the source, wait for tracker synchronization, and read the changed text.
3. Remove the source, wait for synchronization, and confirm it no longer appears
   in `glob` results.

Phoenix records the tool inputs and outputs beneath each stage. Three code-based
annotations mark the assertions that passed. The script retrieves those spans
and annotations through the Phoenix client and saves them in JSON. It makes no
model requests and needs no hosted account or API key.

## Run

Run these commands on your Linux AMD64 workstation as a user with Docker access,
from the directory containing this README. You need Docker and `uv`. The example
uses Python 3.12 and the versions in [requirements.txt](requirements.txt).

```bash
uv venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.txt
docker pull ghcr.io/ctxddev/ctxd-mcp@sha256:44323c50f410893e4d2e7f778e9d1a4da36e4a44a46abeaed25de5c91ff0eb11
```

In one terminal on that workstation, start Phoenix with its own data directory.
Ports 8867 and 8868 must be unused. The server binds only to the loopback address;
its assistant, MCP server and automatic external resources are disabled.

```bash
PHOENIX_HOST=127.0.0.1 PHOENIX_PORT=8867 PHOENIX_GRPC_PORT=8868 \
PHOENIX_WORKING_DIR="$PWD/.phoenix-demo" \
PHOENIX_ALLOW_EXTERNAL_RESOURCES=false PHOENIX_TELEMETRY_ENABLED=false \
PHOENIX_DISABLE_MODEL_COST_AUTO_UPDATE=true \
PHOENIX_DISABLE_AGENT_ASSISTANT=true PHOENIX_ENABLE_MCP_SERVER=false \
.venv/bin/phoenix serve
```

In a second terminal on the same workstation, run the example from this directory:

```bash
.venv/bin/python trace_source_changes.py --endpoint http://127.0.0.1:8867
```

Inspect project `ctxd-source-lifecycle` in Phoenix. Its `source.lifecycle` trace
contains `initial_read`, `after_edit_sync` and `after_removal_sync` stages, with
the actual `ctxd.read` and `ctxd.glob` calls underneath. The
`source_lifecycle_check` annotations use `CODE` as their annotator kind.
`phoenix-source-check.json` contains the same run retrieved from Phoenix, including
the three annotations. Its span count can vary with tracker timing.

Stop your Phoenix process with Ctrl+C when finished. The example container exits
after its MCP session closes, and the synthetic source directory is removed. The
Phoenix data directory remains available for inspection.

## What this checks

The container uses released ctxd 0.1.16 and a digest-pinned image. It mounts only
the generated source directory, read-only, and runs with container networking
disabled. Source changes happen outside the container. The tracer exports to the
separate Phoenix server over loopback HTTP using the explicit `/v1/traces`
endpoint. It clears ambient Phoenix/OTLP settings and records synthetic content
without machine paths or account details.

This is manual instrumentation around real MCP calls, not a maintained automatic
ctxd instrumentor. The assertion after removal checks discovery; it does not
perform a read of the removed path. Polling calls can show the old source until
the tracker synchronizes. The example allows 35 seconds for each change and does
not treat that timeout or observed timing as a service-level guarantee.

The released container rebuilds its context store on each start. Its MCP `grep`
tool is unsupported in this release; this example uses `glob` and `read`.

## References

- [Phoenix manual instrumentation](https://arize.com/docs/phoenix/tracing/how-to-tracing/setup-tracing/instrument)
- [Phoenix span annotations](https://arize.com/docs/phoenix/tracing/how-to-tracing/feedback-and-annotations)
- [ctxd source](https://github.com/ctxd-dev/ctxd)
- [Container source and setup](https://github.com/ctxddev/ctxd-agent-plugin/tree/9f81d373cc5d0eb3aaa0472ad483a924d9865bb1#run-in-docker)
