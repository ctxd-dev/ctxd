---
title: 'ctxfs API'

---

The ctxfs server provides REST endpoints under `/api/ctxfs` and an MCP endpoint
at `POST /mcp`. The managed server listens on the Unix socket
`~/.ctxd/local/ctxfs.sock` by default.

Check the active URL with:

```bash
ctxd endpoint status
```

The managed Unix socket uses local peer trust. Every TCP HTTP request requires
`Authorization: Bearer <token>`, including loopback. Network clients must use
HTTPS with a certificate they trust. REST and HTTP MCP use the same
authentication and authorization policy.

## Operations

| Operation | Endpoint | Important parameters |
| --- | --- | --- |
| Stat a path | `GET /api/ctxfs/stat` | `path` |
| List a directory | `GET /api/ctxfs/ls` | `path`, `limit` |
| Walk a subtree | `GET /api/ctxfs/tree` | `prefix`, `depth`, `limit` |
| Match paths | `GET /api/ctxfs/glob` | `pattern`, `prefix`, `limit` |
| Read a file | `GET /api/ctxfs/read` | `path`, `max_bytes` |
| Read line range | `GET /api/ctxfs/read-lines` | `path`, `start_line`, `end_line` |
| Match a regular expression | `GET /api/ctxfs/grep` | `pattern`, `prefix`, `limit`, `timeout_seconds`, `cursor` |
| Rank indexed documents | `POST /api/ctxfs/search` | JSON: `query`, `prefix`, `limit`, `offset`, `index_revision`, `excerpt_bytes`, `excerpt_limit` |
| Submit tracker changes | `POST /api/ctxfs/submissions` | Tracker-managed submission payload |
| Register a machine | `POST /api/ctxfs/machines/register` | One-use enrollment credential and machine metadata |
| Prepare credential rotation | `POST /api/ctxfs/machines/{machine_id}/credentials/prepare` | Current machine credential |
| Confirm credential rotation | `POST /api/ctxfs/machines/{machine_id}/credentials/confirm` | Replacement key identifier |
| Revoke a machine credential | `DELETE /api/ctxfs/machines/{machine_id}/credential` | Current machine credential |
| Check server health | `GET /health` | `status` credential |
| MCP JSON-RPC | `POST /mcp` | MCP `initialize`, `ping`, `tools/list`, `tools/call` |

REST and MCP paths use canonical ctxfs paths beneath `local-files/<ROOT_ID>/`.
The CLI resolves human-readable aliases only for folders configured on that
client. Other clients use canonical paths.

## Examples

```bash
ctxd files ls Documents
ctxd files glob "Documents/**/*.md" --limit 100
ctxd files read-lines Documents/runbook.md --start 20 --end 40
ctxd files grep "rollback" --prefix Documents --limit 50
```

Use these CLI commands for normal access. They handle the effective local or
network endpoint, endpoint-scoped bearer credential, and folder aliases.

For MCP, configure `ctxd-mcp` as described in the [ctxfs guide](/docs/ctxfs), or
connect an HTTP-capable MCP client to `/mcp` on the effective endpoint. HTTP MCP
clients need `status` for initialization and ping and `read` for tool discovery
and calls. The submission endpoint requires `submit` and enforces the
credential's prefix grants; it is used by the tracker rather than as a general
write API.

Machine lifecycle routes are used by `ctxd machine`; they are not required for
the default local workflow. Registration requires the isolated `enroll` scope.
The remaining machine credential-lifecycle routes accept only the current
machine credential (or an administrator). `/health` is operational but remains
authenticated on HTTP endpoints.

## Bounds and identity

Directory, tree, glob, grep, and search responses are bounded by `limit`. Search
selects up to eight corpus-rare terms from the natural-language query and uses
title-weighted BM25 over the authorized prefix. Its response includes the index
revision, freshness/count metadata, normalized query terms, scores, and a next
offset. Pass the returned revision when requesting another page; pagination
fails explicitly if a concurrent submission changed the index.
Search returns ten results and up to five 800-byte evidence excerpts by default.
Pass `excerpt_bytes=0` and `excerpt_limit=0` together for metadata-only results.

Grep uses ripgrep's Rust regex syntax and a 2-second timeout by default;
`timeout_seconds` accepts 0.1 through 60 seconds. The raw HTTP response exposes
opaque continuation cursors for transport clients, and cursors become stale if
the resumed file changes. The public SDK, CLI, and MCP tool consume non-timeout
transport pages automatically within one timeout budget. They do not retry a
timed-out page and fail explicitly with guidance to narrow the prefix or pattern
when the result cannot be exhaustive. File reads include content identity
metadata so a caller can detect changed content.

Every grep pattern scans files beneath the selected prefix and uses ripgrep for
matching. Continuation works the same way for literal and other regular-expression
patterns. If private tree storage contains files that are absent from the catalog,
all patterns can return those matches and report `catalog_mismatch`.

The current lexical search index is contentless FTS5 with full term-frequency
detail. Existing catalogs remain available on upgrade with search disabled
until an operator runs `ctxd server index rebuild`. Before a non-empty backfill,
ctxfs requires free disk headroom equal to the larger of 256 MiB or twice the
catalog's source bytes. Backfill progress is logged every 10,000 indexed
documents. Schema creation and backfill share an explicit SQLite transaction;
interruption cannot publish a partial ready index. Tracker puts, content updates, moves, and deletes
maintain the index in the same transaction as the catalog mutation, so failed
submissions cannot publish partial index state or revision changes. Directory
prefix counts are also maintained transactionally, allowing freshness metadata
to be returned without rescanning the full catalog on every search.

`read` accepts at most 5,000,000 bytes. The default is 1,000,000 bytes. Use `read-lines` when an agent needs only a section of a large file.

## Errors

- `400` — invalid path, range, pattern, or limit
- `401` — bearer credential is missing, invalid, expired, or revoked
- `403` — the credential lacks the required scope or submission prefix
- `404` — requested path does not exist
- `503` — ctxfs is unavailable; check `ctxd endpoint status`
