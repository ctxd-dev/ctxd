---
title: 'Quickstart'

---

Track a local folder and read it through ctxfs.

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

This saves the backend and starts the local ctxfs server. No API key is required.

## 3. Track a folder

```bash
ctxd folders add ~/Documents --name Documents
ctxd folders list
```

The name `Documents` is also its CLI path alias. Nothing outside folders you
add is tracked.

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

Files are synchronized automatically while the service runs. You do not need to upload them or connect a remote application.

## Next steps

- [Local tracker](/docs/local-tracker) for upgrades, moves, logs, and removal
- [ctxfs](/docs/ctxfs) for the filesystem model and all seven reads
- [Local API](/docs/api) for endpoint parameters
