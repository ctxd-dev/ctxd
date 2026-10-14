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
ctxd config set backend ctxfs
ctxd config get backend
ctxd backend status
```

Selecting `ctxfs` installs and starts the local service when needed. Local operation does not require `CTXD_API_KEY` or `ctxd auth set-key`.

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

The tracker synchronizes configured folders while it is running.

## ctxfs

`ctxd config set backend ctxfs` starts ctxfs when needed. Use
`ctxd backend status` to check the active backend and ctxfs endpoint.

## Environment

| Variable | Purpose |
| --- | --- |
| `CTXD_LOCAL_HOME` | Override daemon state and log directory |
| `CTXD_LOCAL_CTXFS_HOST` | Override the local ctxfs host |
| `CTXD_LOCAL_CTXFS_PORT` | Override the local ctxfs port |
