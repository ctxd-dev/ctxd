---
title: 'Local API'

---

The local ctxfs API is served by the per-user `ctxd` service. Its default base URL is `http://127.0.0.1:8765/api/ctxfs`.

Check the active URL with:

```bash
ctxd config show
```

Loopback requests do not require a ctxd API key. Do not expose this service on a public interface.

## Operations

| Operation | Endpoint | Important parameters |
| --- | --- | --- |
| Stat a path | `GET /stat` | `path` |
| List a directory | `GET /ls` | `path`, `limit` |
| Walk a subtree | `GET /tree` | `prefix`, `depth`, `limit` |
| Match paths | `GET /glob` | `pattern`, `prefix`, `limit` |
| Read a file | `GET /read` | `path`, `max_bytes` |
| Read line range | `GET /read-lines` | `path`, `start_line`, `end_line` |
| Match exact text | `GET /grep` | `pattern`, `prefix`, `limit` |

Paths are relative to the ctxfs root. A tracked folder appears beneath `local-files/<ROOT_ID>/`.

## Examples

```bash
curl "http://127.0.0.1:8765/api/ctxfs/ls?path=local-files/<ROOT_ID>"
curl "http://127.0.0.1:8765/api/ctxfs/glob?pattern=**/*.md&prefix=local-files/<ROOT_ID>"
curl "http://127.0.0.1:8765/api/ctxfs/read-lines?path=local-files/<ROOT_ID>/runbook.md&start_line=20&end_line=40"
curl "http://127.0.0.1:8765/api/ctxfs/grep?pattern=rollback&prefix=local-files/<ROOT_ID>&limit=50"
```

## Bounds and identity

Directory, tree, glob, and grep responses are bounded by `limit`. Check the response's completeness fields before treating a result as exhaustive. File reads include content identity metadata so a caller can detect changed content.

`read` accepts at most 5,000,000 bytes. The default is 1,000,000 bytes. Use `read-lines` when an agent needs only a section of a large file.

## Errors

- `400` — invalid path, range, pattern, or limit
- `404` — requested path does not exist
- `503` — ctxfs is unavailable; check `ctxd service status`
