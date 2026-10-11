---
title: 'Quickstart'

---

Track a local folder and read it through ctxfs.

## 1. Install

Install the local tracker bundle supplied by ctxd. Replace the example path with the directory containing the package artifacts:

```bash
uv tool install --force --find-links /path/to/local-file-daemon-dist local-file-daemon
ctxd version
```

## 2. Enable local ctxfs

```bash
ctxd config set backend ctxfs
ctxd config show
```

This installs and starts the per-user service. It runs the folder tracker and local ctxfs server together. No API key is required.

## 3. Track a folder

```bash
ctxd folders add ~/Documents --name Documents
ctxd folders list
```

Keep the `ROOT_ID` shown by `ctxd folders list`. Files from the folder appear beneath:

```text
local-files/<ROOT_ID>/
```

## 4. Verify the service

```bash
ctxd service status
ctxd status
```

Look for `Service running: yes`, `Backend: ctxfs`, and `ctxfs reachable: yes`.

To request an immediate tracking pass:

```bash
ctxd daemon run --once
```

## 5. Read from ctxfs

```bash
curl "http://127.0.0.1:8765/api/ctxfs/tree?prefix=local-files/<ROOT_ID>&limit=100"
curl "http://127.0.0.1:8765/api/ctxfs/read?path=local-files/<ROOT_ID>/notes.md"
curl "http://127.0.0.1:8765/api/ctxfs/grep?pattern=deployment&prefix=local-files/<ROOT_ID>&limit=20"
```

Use the URL printed by `ctxd config show` if you changed the local host or port.

Files are synchronized automatically while the service runs. You do not need to upload them or connect a remote application.

## Next steps

- [Local tracker](/docs/local-tracker) for upgrades, moves, logs, and removal
- [ctxfs](/docs/ctxfs) for the filesystem model and all seven reads
- [Local API](/docs/api) for endpoint parameters
