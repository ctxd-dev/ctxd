---
title: 'ctxfs'
---

ctxfs presents tracked local content as a filesystem-shaped tree that agents can inspect with explicit, bounded operations. It runs locally with the tracker and uses exact, path-oriented reads.

## Install and start

ctxfs ships in the ctxd package. Install ctxd, then select the backend:

```bash
pip install ctxd
ctxd config set backend ctxfs
ctxd config show
```

The default root is `~/.ctxd/ctxfs`, and the default HTTP endpoint is `http://127.0.0.1:8765`. The service manages both pieces; do not launch a second ctxfs process.

## Path model

Every tracked folder has a stable root ID:

```text
local-files/<ROOT_ID>/<path-relative-to-the-tracked-folder>
```

For example, if root `abc123` tracks `~/Projects/example`, then `~/Projects/example/docs/runbook.md` appears at:

```text
local-files/abc123/docs/runbook.md
```

Find root IDs with `ctxd folders list`.

## Read operations

| Operation | Purpose |
| --- | --- |
| `stat` | Inspect one path and its metadata |
| `ls` | List one directory |
| `tree` | Walk a subtree to an optional depth |
| `glob` | Match paths with a glob pattern |
| `read` | Read a complete file up to a byte bound |
| `read_lines` | Read an inclusive line range |
| `grep` | Match exact text beneath a prefix |

ctxfs deliberately exposes no shell and no arbitrary commands. `grep` is exact text matching, not semantic search.

## Verify a tracked file

```bash
ctxd folders list
ctxd daemon run --once
curl "http://127.0.0.1:8765/api/ctxfs/tree?prefix=local-files/<ROOT_ID>&limit=100"
curl "http://127.0.0.1:8765/api/ctxfs/read?path=local-files/<ROOT_ID>/docs/runbook.md"
```

See the [local API reference](/docs/api) for every endpoint and parameter.

## Bounded results

Listing and matching operations accept a `limit`. Read operations accept byte or line bounds. Responses include completeness information; callers must not treat a truncated response as exhaustive.

File reads also include content identity information. An agent can use it to detect whether a cited file changed between reads.

## MCP

The running ctxfs service exposes the seven read operations through MCP at
`/mcp` on its configured local endpoint. MCP calls use the same in-process read
service as the REST API.

For MCP clients that require stdio, configure the compatibility bridge:

```text
ctxd-mcp
```

The bridge resolves the configured local ctxfs endpoint itself. MCP tools do not
accept endpoint, filesystem-root, or user-identity arguments.

## Local data

The source folder remains authoritative. The tracker observes it and applies changes to ctxfs. Removing a tracked root does not delete the source folder.

Use `ctxd service uninstall --purge-state --clear-credentials` only when you intend to remove all ctxd-managed local state.
