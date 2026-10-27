---
title: 'Overview'

---

ctxd makes indexed content available as agent-readable context. A deployment
can use two components:

1. **ctxfs** stores indexed content and exposes it through filesystem-shaped
   paths with bounded read tools.
2. The optional **local tracker** watches only the folders you choose and sends
   their content to ctxfs.

## Basic workflow

1. **Install the `ctxd` CLI.**
2. **Add a folder** with `ctxd folders add`. The local tracker indexes that
   folder, and nothing outside the folders you add is tracked.
3. **Read through the ctxd CLI** with `ls`, `tree`, `glob`, `stat`, `read`,
   `read-lines`, and `grep` operations. The CLI uses the configured local or
   network ctxfs endpoint.

## Architecture

| Component | Role | Default location |
| --- | --- | --- |
| [Local tracker](/docs/local-tracker) | Watches selected folders and applies changes | Per-user background process |
| [ctxfs](/docs/ctxfs) | Stores indexed content and exposes filesystem-shaped paths | `~/.ctxd/ctxfs` |
| [CLI](/docs/cli) | Configures ctxfs and tracked folders, and reads indexed content | `ctxd` |
| [ctxfs API](/docs/api) | Provides REST and MCP access | Unix socket or HTTPS endpoint |

The tracker and ctxfs server are separate processes. When `ctxd` manages ctxfs
locally over its default Unix socket, starting the tracker ensures the local
ctxfs process is running. When configured to use an external endpoint, starting
the tracker verifies that it can authenticate to that server instead of
launching a local ctxfs process.

## Authentication

The managed local Unix socket uses operating-system peer trust. HTTP endpoints
require a scoped API key, and non-loopback serving requires HTTPS.

## What agents can do

ctxfs is path-oriented. Agents can inspect directories, match paths, read
complete files or line ranges, and grep with regular expressions. Results report
bounds and content identity so callers can tell when output was truncated or
content changed.

ctxfs is not a semantic-search index and does not hide the file tree behind ranked chunks.

## Next steps

- [Quickstart](/docs/quickstart) to track your first folder
- [Local tracker](/docs/local-tracker) for installation and service management
- [ctxfs](/docs/ctxfs) for paths and read operations
