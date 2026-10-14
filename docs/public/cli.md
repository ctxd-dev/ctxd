---
title: 'CLI'

---

The `ctxd` CLI installs and manages the local tracker and ctxfs service.

## Install

```bash
pip install ctxd
ctxd version
```

## Configuration

```bash
ctxd config set backend ctxfs
ctxd config show
```

Selecting `ctxfs` installs and starts the local service when needed. Local operation does not require `CTXD_API_KEY` or `ctxd auth set-key`.

## Folders

```bash
ctxd folders add ~/Documents --name Documents
ctxd folders list
ctxd folders repair-move <ROOT_ID> /new/path/to/Documents --name Documents
ctxd folders remove <ROOT_ID>
```

Removing a root stops tracking it. It does not delete the source folder.

## Tracker

```bash
ctxd daemon run --once
ctxd daemon run --interval-seconds 60
ctxd status
```

Use `--once` to apply changes immediately while diagnosing a folder.

## Service

```bash
ctxd service status
ctxd service stop
ctxd service start
ctxd service restart
ctxd service uninstall
```

To remove local state and credentials as well as the service registration:

```bash
ctxd service uninstall --purge-state --clear-credentials
```

## Environment

| Variable | Purpose |
| --- | --- |
| `CTXD_LOCAL_HOME` | Override daemon state and log directory |
| `CTXD_LOCAL_CTXFS_HOST` | Override the local ctxfs host |
| `CTXD_LOCAL_CTXFS_PORT` | Override the local ctxfs port |
