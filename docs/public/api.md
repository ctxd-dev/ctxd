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

Directory, tree, glob, and grep responses are bounded by `limit`. Check the response's completeness fields before treating a result as exhaustive. Grep uses a 2-second timeout by default; `timeout_seconds` accepts 0.1 through 60 seconds. A resumable result sets `continuation_required` and provides both `next_cursor` and a `continuation_hint`; repeat the same pattern and prefix with that value as `cursor`. After repeated timeout stops, increase `timeout_seconds` or narrow the prefix without restarting from the beginning. Cursors are opaque and become stale if the file being resumed changes. File reads include content identity metadata so a caller can detect changed content.

Grep transparently candidate-indexes plain, metacharacter-free patterns of at
least three characters, then verifies candidates with the normal regex matcher.
Other regex patterns use the scan path. Indexed literal completeness covers the
ctxfs catalog and intentionally excludes files placed directly in private tree
storage; use a regex scan when diagnosing such catalog/storage drift. Older
catalogs receive a one-time synchronous index backfill when first opened.

`read` accepts at most 5,000,000 bytes. The default is 1,000,000 bytes. Use `read-lines` when an agent needs only a section of a large file.

## Errors

- `400` — invalid path, range, pattern, or limit
- `401` — bearer credential is missing, invalid, expired, or revoked
- `403` — the credential lacks the required scope or submission prefix
- `404` — requested path does not exist
- `503` — ctxfs is unavailable; check `ctxd endpoint status`
