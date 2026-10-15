---
title: 'Quickstart'

---

Start with managed local ctxfs, optionally track a folder, and read it through
the CLI. See the [ctxfs guide](/docs/ctxfs) for a network deployment.

## 1. Install

Install ctxd from PyPI. The package includes the CLI, local tracker, and ctxfs:

```bash
pip install ctxd
ctxd --version
```

## 2. Enable local ctxfs

```bash
ctxd backend set ctxfs
ctxd config get backend
```

This saves the backend and starts ctxfs at the default managed local endpoint.
If you explicitly configured an endpoint, the command checks that server
instead. The managed-local workflow does not require an API key.

## 3. Optionally track a folder

```bash
ctxd folders add ~/Documents --name Documents
ctxd folders list
```

Do this only when this machine should contribute content. The name `Documents`
is also its CLI path alias. A server can instead run with an empty store or
serve content submitted by other authorized tracker clients.

## 4. Verify the service

```bash
ctxd backend status
ctxd tracker status
```

Look for `Backend: ctxfs` and `ctxfs healthy: True`.

Start the separate folder-tracker process:

```bash
ctxd tracker start
```

## 5. Read from ctxfs

```bash
ctxd files tree Documents --depth 2 --limit 100
ctxd files read Documents/notes.md
ctxd files grep "deployment" --prefix Documents --limit 20
```

Tracked files are synchronized while the tracker runs. The same commands read
from a configured authenticated network endpoint.

## Next steps

- [Local tracker](/docs/local-tracker) for upgrades, moves, logs, and removal
- [ctxfs](/docs/ctxfs) for the filesystem model and all seven reads
- [ctxfs API](/docs/api) for endpoint parameters and authentication
