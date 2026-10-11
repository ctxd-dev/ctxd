---
title: 'Overview'

---

ctxd turns folders on your computer into local, agent-readable context. It uses two pieces:

- The **local tracker** watches only the folders you choose.
- **ctxfs** presents the tracked content as a path-oriented filesystem with bounded read tools.

ctxd does not connect to Google Drive, Slack, GitHub, Calendar, or other remote applications. Put the material you want an agent to use in a local folder, then track that folder.

## How it works

1. **Install** the `ctxd` command and select the local `ctxfs` backend.
2. **Choose folders** with `ctxd folders add`. Nothing outside those folders is tracked.
3. **Keep them current.** The local service detects file changes and updates ctxfs.
4. **Read locally** with `ls`, `tree`, `glob`, `stat`, `read`, `read_lines`, and `grep` operations.

## Local architecture

| Component | Role | Default location |
| --- | --- | --- |
| [Local tracker](/docs/local-tracker) | Watches selected folders and applies changes | Per-user background service |
| [ctxfs](/docs/ctxfs) | Stores and exposes the local context tree | `~/.ctxd/ctxfs` |
| [CLI](/docs/cli) | Configures folders and manages the service | `ctxd` |
| [Local API](/docs/api) | Provides the seven ctxfs read operations | `http://127.0.0.1:8765` |

The tracker and ctxfs server run together as one per-user `ctxd` service. Local ctxfs reads do not require a ctxd API key.

## What agents can do

ctxfs is exact and path-oriented. Agents can inspect directories, match paths, read complete files or line ranges, and grep for exact text. Results report bounds and content identity so callers can tell when output was truncated or content changed.

ctxfs is not a semantic-search index and does not hide the file tree behind ranked chunks.

## Next steps

- [Quickstart](/docs/quickstart) to track your first folder
- [Local tracker](/docs/local-tracker) for installation and service management
- [ctxfs](/docs/ctxfs) for paths and read operations
