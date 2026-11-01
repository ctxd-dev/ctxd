---
title: 'ctxfs'
---

ctxfs stores indexed content and metadata and exposes them through a
filesystem-shaped tree that agents can inspect with explicit, bounded
operations.

## Install and start

ctxfs ships in the ctxd package and is the storage and retrieval service used
by `ctxd` commands. There is no backend-selection step. When no external
endpoint is configured, a command that needs ctxfs starts the local process:

```bash
pip install ctxd
ctxd endpoint status
```

The default data root is `~/.ctxd/ctxfs`. The managed server listens through the
Unix socket `~/.ctxd/local/ctxfs.sock`; `ctxd endpoint status` checks that endpoint
and prints its URL and health. An explicitly configured URL or socket is checked
without launching a second server. Do not launch a second server against the same
data root.

## Deployment roles

A deployment can separate three roles:

1. The **server** owns indexed content and metadata and exposes REST and MCP.
2. A **writer** runs the tracker and submits selected folders.
3. A **reader** uses `ctxd files`, REST, or MCP against the configured endpoint.

The server may start empty and does not need a tracker or local folders. One
machine may run all three roles. This release provides a single ctxfs namespace
with multiple scoped credentials; it does not claim multi-tenant isolation.

## Standalone and network server

Run a trusted local socket or authenticated loopback listener in the foreground:

```bash
ctxd server serve --root /srv/ctxfs --socket /run/user/1000/ctxfs.sock
ctxd server serve --root /srv/ctxfs --host 127.0.0.1 --port 8765
```

For another machine to connect, direct TLS and explicit network opt-in are
required:

```bash
ctxd server serve --root /srv/ctxfs --host 0.0.0.0 --port 8443 \
  --allow-network --tls-cert /etc/ctxfs/server.crt \
  --tls-key /etc/ctxfs/server.key \
  --advertised-url https://ctxfs.example.net:8443
```

The certificate must be valid for the hostname clients use. Keep the TLS private
key owner-only (for example, mode `0600`). Install a private CA in each client's
trust store when a public CA is not used. Do not disable
certificate verification or put a bearer token in a URL. Reverse-proxy and
background-service installation are not provided by these commands.

## Credentials and clients

On the server, create separate least-privilege credentials:

```bash
ctxd keys --root /srv/ctxfs create --name reader --scope read --scope status
ctxd keys --root /srv/ctxfs create --name writer --scope submit --scope status \
  --prefix local-files/ROOT_ID
ctxd keys --root /srv/ctxfs list
```

The token is shown once. On the writer client, `ctxd folders add` records each
folder's generated `prefix` in `~/.ctxd/config.json` (or the file selected by
`CTXD_CONFIG_PATH`). Give the server administrator the exact prefix; repeat
`--prefix` when a writer may submit multiple roots. `ctxd folders list` does not
print generated prefixes. On a reader or writer client:

```bash
ctxd login --endpoint https://ctxfs.example.net:8443
ctxd endpoint set https://ctxfs.example.net:8443
ctxd status --endpoint https://ctxfs.example.net:8443
ctxd endpoint status
```

`read` permits REST reads plus MCP tool discovery and calls. `submit` permits
changes only beneath its server-assigned prefix grants. `status` permits health
checks and MCP initialization and ping; an HTTP MCP reader therefore normally
needs both `read` and `status`. `admin` grants every capability. Unix sockets
use explicit local peer trust; every TCP request requires a bearer credential,
including loopback.

To rotate a credential, run `ctxd keys --root /srv/ctxfs rotate KEY_ID`.
Rotation revokes the old key immediately and prints the replacement once. On
each client, run `ctxd logout --endpoint URL`, then `ctxd login --endpoint URL`
and enter the replacement. Verify it with `ctxd status --endpoint URL`.
`ctxd keys --root /srv/ctxfs revoke KEY_ID` also takes effect immediately.

## Path model

Every tracked folder has a stable root ID:

```text
local-files/<ROOT_ID>/<path-relative-to-the-tracked-folder>
```

For example, if root `abc123` tracks `~/Projects/example`, then `~/Projects/example/docs/runbook.md` appears at:

```text
local-files/abc123/docs/runbook.md
```

The CLI resolves folder names configured on that client to these prefixes, so a
writer can use a path such as `Example/docs/runbook.md`. A separate reader that
does not have that local folder configuration uses the canonical
`local-files/<ROOT_ID>/docs/runbook.md` path instead.

## Read operations

| Operation | Purpose |
| --- | --- |
| `stat` | Inspect one path and its metadata |
| `ls` | List one directory |
| `tree` | Walk a subtree to an optional depth |
| `glob` | Match paths with a glob pattern |
| `read` | Read a complete file up to a byte bound |
| `read_lines` | Read an inclusive line range |
| `grep` | Match a regular expression beneath a prefix |

ctxfs deliberately exposes no shell and no arbitrary commands. `grep` uses
ripgrep's Rust regular-expression syntax, not semantic search. ripgrep is a
required runtime dependency (version 13.0.0 or newer); ctxfs never substitutes
a different regex engine.

Plain patterns of at least three characters that contain no regular-expression
metacharacters use a local trigram index to identify candidate catalog files.
ctxfs still verifies every candidate with the regular-expression matcher, so
case sensitivity, matching lines, and offsets are unchanged. General regular
expressions and shorter literals use the scan path.

Agents should scope retrieval before searching broadly:

1. Inspect only tracked roots. Use
   `ctxd files tree local-files --depth 1` from the CLI, or call the MCP `tree`
   tool with `prefix=local-files` and `depth=1`.
2. Select the relevant canonical `local-files/<ROOT_ID>` path returned by that
   tree operation.
3. Reuse that exact path as the `grep` prefix (for example,
   `prefix=local-files/ROOT_ID` after substituting the returned ID).
4. Broaden the prefix only if the scoped search does not find enough context.

This keeps retrieval focused and makes search cost easier to understand without
changing grep semantics.

Indexed literal results are authoritative over cataloged ctxfs entries. They do
not inventory private tree storage for files added outside ctxfs. Regex searches
retain the scan path and its `catalog_mismatch` diagnostics for that kind of
storage drift. The index is maintained with catalog writes; opening a catalog
created by an older release performs a one-time synchronous backfill, which
temporarily increases startup work and permanently increases local index storage.

## Verify a tracked file

```bash
ctxd folders list
ctxd tracker start
ctxd files tree Example --depth 2 --limit 100
ctxd files read Example/docs/runbook.md
```

See the [ctxfs API reference](/docs/api) for every endpoint and parameter.

## Bounded results

Listing and matching operations accept a `limit`. Read operations accept byte or line bounds. Responses include completeness information; callers must not treat a truncated response as exhaustive.

Grep accepts a per-request `timeout_seconds` from 0.1 to 60 seconds and defaults
to 2 seconds. The SDK, CLI, and MCP tool automatically resume transport pages;
agents never manage cursors. If a safety bound prevents an exhaustive result,
grep fails explicitly and asks the caller to narrow its prefix or pattern. The
raw HTTP endpoint retains opaque cursors for transport implementations.

File reads also include content identity information. An agent can use it to detect whether a cited file changed between reads.

## MCP

The running ctxfs service exposes the seven read operations through MCP at
`POST /mcp` on its configured endpoint. MCP calls use the same in-process read
service as the REST API.

The managed endpoint is a Unix socket, so the simplest MCP client configuration
is the stdio bridge installed by the same `ctxd` package:

```json
{
  "mcpServers": {
    "ctxfs": {
      "command": "ctxd-mcp"
    }
  }
}
```

The bridge resolves the configured ctxfs endpoint and endpoint-scoped
credential itself. MCP tools do not accept endpoint, filesystem-root, or
user-identity arguments.
