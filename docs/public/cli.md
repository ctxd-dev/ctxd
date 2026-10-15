---
title: 'CLI'

---

The `ctxd` CLI installs and manages the local tracker and ctxfs service.

## Install

```bash
pip install ctxd
ctxd --version
```

## Configuration

```bash
ctxd backend set ctxfs
ctxd config get backend
ctxd backend status
```

`backend set` saves the selection. It starts ctxfs for the default managed local
socket, or checks the effective endpoint when a URL or socket was explicitly
configured. Local operation does not require an API key.

## Folders

```bash
ctxd folders add ~/Documents --name Documents
ctxd folders list
ctxd folders remove Documents
```

Removing a root stops tracking it. It does not delete the source folder.

## Tracker

```bash
ctxd tracker start
ctxd tracker status
ctxd tracker stop
```

The tracker synchronizes configured folders while it is running. It may run
with zero folders; adding folders is optional unless this machine contributes
content.

## Files

Use a configured folder name as the first path segment:

```bash
ctxd files tree Documents --depth 2 --limit 100
ctxd files ls Documents
ctxd files glob "Documents/**/*.md" --limit 100
ctxd files stat Documents/notes.md
ctxd files read Documents/notes.md --max-bytes 100000
ctxd files read-lines Documents/notes.md --start 20 --end 40
ctxd files grep "deployment" --prefix Documents --limit 20
```

Every listing and matching command is bounded. Inspect `complete` and
`stopped_by` in JSON output before treating a result as exhaustive.

## ctxfs

`ctxd backend set ctxfs` starts ctxfs only for the default managed endpoint. Use
`ctxd backend status` to check the active backend, effective endpoint, endpoint
source, health, and whether ctxd manages the server process locally.

## MCP

For the default Unix-socket installation, configure MCP clients to launch the
stdio bridge:

```text
ctxd-mcp
```

The ctxfs server also accepts MCP JSON-RPC requests over HTTP at `POST /mcp` on
its configured endpoint.

## Environment

| Variable | Purpose |
| --- | --- |
| `CTXD_CONFIG_PATH` | Override the CLI configuration file |
| `CTXD_BACKEND` | Override the active backend (`ctxfs` or `remote`) |
| `CTXD_CTXFS_SOCKET` | Use ctxfs through the selected Unix socket |
| `CTXD_CTXFS_URL` | Use ctxfs through the selected HTTP URL |
