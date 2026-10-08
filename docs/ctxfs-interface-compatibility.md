# Backend Interface Compatibility Design

## Problem

The public `ctxd` package exposes hosted MCP commands (`search`, `fetch`, and
`profile`) but cannot talk to the local ctxfs read contract used by the local
tracker. Adding a backend-specific CLI namespace for every runtime would make
the command surface harder to use over time.

`ctxd` should keep one CLI and SDK surface. Backend configuration should decide
whether a command talks to hosted MCP, local ctxfs, or a future backend.

## Goals

- Keep hosted behavior as the default.
- Add backend selection through config, environment, and per-command override.
- Add a ctxfs adapter that supports Unix socket and loopback HTTP endpoints.
- Keep hosted API keys scoped to hosted calls; local ctxfs reads do not use
  `CTXD_API_KEY`.
- Add backend-neutral file commands for path-oriented operations.

## CLI Shape

Hosted behavior remains unchanged:

```bash
ctxd search "text:deployment"
ctxd fetch doc-123
ctxd profile
```

Backend selection:

```bash
ctxd config set backend hosted
ctxd config set backend ctxfs
ctxd config get backend
```

For local ctxfs:

```bash
ctxd config set backend ctxfs
ctxd search "ctxfs" --prefix local-files/<root-id> --limit 20
ctxd fetch local-files/<root-id>/README.md
ctxd profile
ctxd files tree local-files/<root-id> --depth 2
ctxd files read-lines local-files/<root-id>/README.md --start 1 --end 20
```

The `files` commands are backend-neutral. A backend that cannot implement a file
operation should return a clear unsupported-operation error.

## SDK Shape

`Client` and `AsyncClient` become backend-aware:

```python
from ctxd import Client

hosted = Client(backend="hosted")
hosted.search("text:deployment")

local = Client(backend="ctxfs")
local.search("deployment", prefix="local-files/<root-id>", limit=20)
local.fetch("local-files/<root-id>/README.md")
local.files.tree("local-files/<root-id>", depth=2)
```

The ctxfs adapter exposes models for bounded directory entries, reads, line
reads, grep matches, and service status.

## Endpoint Resolution

Hosted uses `CTXD_BASE_URL` and hosted config. Ctxfs endpoint resolution is
separate:

1. `CTXD_CTXFS_URL`
2. `CTXD_CTXFS_SOCKET`
3. stored ctxd config
4. local daemon config
5. `unix://~/.ctxd/local/ctxfs.sock` when present
6. `http://127.0.0.1:8765`

Unix socket endpoints use `httpx` UDS transport with a synthetic base URL.

## Implementation Plan

1. Add backend config and keep hosted behavior default.
2. Add ctxfs models and ctxfs sync/async clients.
3. Route `Client`, `AsyncClient`, and CLI commands through the selected backend.
4. Add backend-neutral `ctxd files ...` commands.
5. Document the local ctxfs workflow.

## Verification

- Unit tests for backend config and endpoint resolution.
- Unit tests proving hosted behavior remains default.
- Unit tests proving ctxfs requests do not send hosted credentials.
- CLI tests for backend config, ctxfs `search`, and `files` commands.
