---
title: 'Quickstart'

---

Start with ctxfs running locally, optionally track a folder, and read it through
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

This saves the backend selection and starts a local ctxfs process using the
default Unix socket. If you explicitly configured an endpoint, the command
checks that server instead of starting a local process. Connections through the
default local Unix socket do not require an API key.

## 3. Optionally track a folder

```bash
ctxd folders add ~/Documents --name Documents
ctxd folders list
```

Do this only when this machine should contribute content. The name `Documents`
is also its CLI path alias.

## 4. Start the tracker

```bash
ctxd tracker start
```

The tracker runs separately from ctxfs and keeps the folders you added
synchronized.

Verify both processes:

```bash
ctxd backend status
ctxd tracker status
```

Look for `Backend: ctxfs`, `ctxfs healthy: True`, and `tracker running: True`.
The status commands only report current state; they do not start either
process.

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
