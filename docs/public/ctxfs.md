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
machine may run all three roles. Each server owns one ctxfs namespace and may
issue multiple scoped credentials for its readers and writers.

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
| `search` | Rank indexed documents for a natural-language lexical query |

ctxfs deliberately exposes no shell and no arbitrary commands. `grep` uses
ripgrep's Rust regular-expression syntax. `search` is a deterministic lexical
retriever: it selects up to eight corpus-rare query terms and ranks authorized
documents with title-weighted BM25. It does not use embeddings or expand the
caller's path scope. ripgrep is a required runtime dependency (version 13.0.0
or newer); ctxfs never substitutes a different regex engine.

The full-detail FTS5 search index is built transactionally for an existing
catalog. Subsequent puts, content updates, moves, and deletes update it in the
same transaction as the catalog mutation. Search responses report catalog and
index counts, freshness, the selected normalized terms, and an index revision.
Clients must echo that revision on later offset pages; a concurrent index update
makes the page request fail rather than silently changing result order.
Search returns ten results and up to five 800-byte evidence excerpts by default.
Pass `excerpt_bytes=0` and `excerpt_limit=0` together for metadata-only results.
An existing catalog without a ready index remains available for reads, grep and
writes, while search returns `503` until an operator runs `server index rebuild`.
Before that explicit backfill, ctxfs verifies free disk headroom equal to at
least twice the catalog's source bytes, with a 256 MiB floor. It logs progress
every 10,000 documents. Index schema creation and backfill use one explicit
transaction, so an interruption cannot publish a partial ready index.
Search maintains index and catalog counts for directory prefixes in the same
transaction as content changes. Freshness checks therefore avoid a full catalog
scan for ordinary root and directory-scoped queries; exact-file or otherwise
uncached prefixes retain the authoritative count fallback.

### Search-index operations

Operators can inspect index health without exposing query or document data:

```sh
ctxd server index status --json
```

The command reports build and schema state, revision, update time, index and
catalog counts, freshness, and overall health. A non-healthy result exits with
status 1. Rebuild the derived index from the authoritative catalog with:

```sh
ctxd server index rebuild --json
```

Use `--root PATH` immediately after `index` for a non-default server data root.
A successful rebuild advances the index revision so pagination clients restart
safely. Schedule rebuild during a low-traffic maintenance window.

Search and rebuild diagnostics record latency, success/failure, freshness,
selected-term count, bounded response size, excerpt fallback count, revision,
result count, entry counts, and rebuild progress. They never record query text,
paths, excerpts, or document content.

Every pattern scans files beneath the selected prefix and uses ripgrep for final
matching. Choose the narrowest relevant canonical root before searching; broad
prefixes may require multiple internal continuation pages.

Grep invokes ripgrep directly over that filesystem prefix. It does not enumerate
the SQLite catalog or hash unrelated files before matching. On a fresh scan,
only files that produce matches are checked against catalog metadata and
assigned content hashes. Resuming a `file_start` cursor also snapshots its named
anchor file, even if that file has no match, to validate the cursor before
continuing. Therefore, a complete grep response means the visible
filesystem scope was searched exhaustively and its returned matches were
verified; it does not assert that every catalog row still has a corresponding
file. Use `stat`, `tree`, or writer-status diagnostics to investigate broader
catalog drift.

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

All patterns use the same filesystem-snapshot scan behavior. A completed scan
reports `catalog_mismatch` when matching files in private tree storage are not
represented by the catalog. If a safety bound stops the scan, its operational
reason (`limit`, `timeout`, or `max_bytes`) takes precedence, even when a catalog
mismatch was observed earlier.

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

Grep accepts `timeout_seconds` from 0.1 to 60 seconds and defaults to 2 seconds.
For the SDK, CLI, and MCP tool, that value is one budget for the whole grep call.
Those surfaces automatically resume non-timeout transport pages; agents never
manage cursors. They do not retry a timed-out page. If the budget or another
safety bound prevents an exhaustive result, grep fails explicitly and asks the
caller to narrow its prefix or pattern. The raw HTTP endpoint retains opaque
cursors and per-request page bounds for transport implementations.

File reads also include content identity information. An agent can use it to detect whether a cited file changed between reads.

## MCP

The running ctxfs service exposes the eight read operations through MCP at
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
