---
title: 'ctxfs API'

---

The ctxfs server provides REST endpoints under `/api/ctxfs` and an MCP endpoint
at `POST /mcp`. The managed server listens on the Unix socket
`~/.ctxd/local/ctxfs.sock` by default.

Check the active URL with:

```bash
ctxd backend status
```

The managed Unix socket uses local peer trust. Every HTTP request requires
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
| Match exact text | `GET /api/ctxfs/grep` | `pattern`, `prefix`, `limit` |
| MCP JSON-RPC | `POST /mcp` | MCP `initialize`, `ping`, `tools/list`, `tools/call` |

REST and MCP paths use canonical ctxfs paths beneath `local-files/<ROOT_ID>/`.
The CLI resolves human-readable folder aliases before calling these interfaces.

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
connect an HTTP-capable MCP client to `/mcp` on the effective endpoint.

## Bounds and identity

Directory, tree, glob, and grep responses are bounded by `limit`. Check the response's completeness fields before treating a result as exhaustive. File reads include content identity metadata so a caller can detect changed content.

`read` accepts at most 5,000,000 bytes. The default is 1,000,000 bytes. Use `read-lines` when an agent needs only a section of a large file.

## Errors

- `400` — invalid path, range, pattern, or limit
- `401` — bearer credential is missing, invalid, expired, or revoked
- `403` — the credential lacks the required scope or submission prefix
- `404` — requested path does not exist
- `503` — ctxfs is unavailable; check `ctxd backend status`
