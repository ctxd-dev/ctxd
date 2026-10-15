---
title: 'Overview'

---

ctxd makes indexed content available as agent-readable context. A deployment
can use two pieces:

1. The optional **local tracker** watches only the folders you choose and sends
   their content to ctxfs.
2. **ctxfs** stores indexed content and exposes it through filesystem-shaped
   paths with bounded read tools.

Add a folder when this machine should contribute local content. A ctxfs server
can run and serve its existing store without a local tracker or tracked folder.

## How it works

1. **Install** the `ctxd` command and select the local `ctxfs` backend.
2. **Optionally choose folders** with `ctxd folders add` when this machine should
   contribute content. Nothing outside those folders is tracked.
3. **Keep contributed folders current.** The tracker detects file changes and
   updates ctxfs.
4. **Read through the ctxd CLI** with `ls`, `tree`, `glob`, `stat`, `read`,
   `read-lines`, and `grep` operations. The CLI uses the configured local or
   network ctxfs endpoint.

## Architecture

| Component | Role | Default location |
| --- | --- | --- |
| [Local tracker](/docs/local-tracker) | Watches selected folders and applies changes | Per-user background process |
| [ctxfs](/docs/ctxfs) | Stores indexed content and exposes filesystem-shaped paths | `~/.ctxd/ctxfs` |
| [CLI](/docs/cli) | Configures folders and reads ctxfs | `ctxd` |
| [ctxfs API](/docs/api) | Provides REST and MCP access | Unix socket or HTTPS endpoint |

The tracker and ctxfs server are separate processes. With the default managed
local endpoint, starting the tracker ensures ctxfs is running. An explicitly
configured endpoint is checked without starting another server. The managed
local Unix socket uses operating-system peer trust; HTTP endpoints require a
scoped API key, and non-loopback serving requires HTTPS.

## What agents can do

ctxfs is exact and path-oriented. Agents can inspect directories, match paths, read complete files or line ranges, and grep for exact text. Results report bounds and content identity so callers can tell when output was truncated or content changed.

ctxfs is not a semantic-search index and does not hide the file tree behind ranked chunks.

## Next steps

- [Quickstart](/docs/quickstart) to track your first folder
- [Local tracker](/docs/local-tracker) for installation and service management
- [ctxfs](/docs/ctxfs) for paths and read operations
