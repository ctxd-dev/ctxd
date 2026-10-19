---
title: 'Quickstart'

---

Start with local ctxfs, optionally track a folder, and read it through
the CLI. See the [ctxfs guide](/docs/ctxfs) for a network deployment.

## 1. Install

Install ctxd from PyPI. The package includes the CLI, local tracker, and ctxfs:

```bash
pip install ctxd
ctxd --version
```

## 2. Verify local ctxfs

```bash
ctxd endpoint status
```

This starts ctxfs at the default local Unix socket and checks its health. If you
configured an endpoint, the command checks that server instead. The default
Unix-socket workflow does not require an API key.

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
ctxd endpoint status
ctxd tracker status
```

Look for `ctxfs healthy: true`.

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
from a configured authenticated network endpoint when the client has the same
folder alias. A separate reader uses canonical paths such as
`local-files/<ROOT_ID>/notes.md`.

## Next steps

- [Local tracker](/docs/local-tracker) for upgrades, moves, logs, and removal
- [ctxfs](/docs/ctxfs) for the filesystem model and all seven reads
- [ctxfs API](/docs/api) for endpoint parameters and authentication
