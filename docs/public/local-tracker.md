---
title: 'Local tracker'
---

The local tracker watches folders you select and keeps their ctxfs representation current. It runs with the local ctxfs server as one per-user background service.

## Requirements

- A supported macOS, Linux, or Windows user environment
- Python 3.11 or 3.12 with `pip`
- A local folder you want ctxd to track

## Install

Install ctxd from PyPI:

```bash
pip install ctxd
ctxd --version
```

The package includes the CLI, tracker, and ctxfs. ctxfs is not installed as a separate system service.

## Start in ctxfs mode

```bash
ctxd config set backend ctxfs
ctxd tracker start
ctxd backend status
```

Selecting the `ctxfs` backend installs and starts the per-user service. The service starts again at login and restarts after failures.

## Add folders

```bash
ctxd folders add ~/Documents --name Documents
ctxd folders add ~/Projects/example --name Example
ctxd folders list
```

Each folder receives a stable `ROOT_ID`. Its ctxfs prefix is `local-files/<ROOT_ID>/`.

## Monitor

```bash
ctxd tracker status
ctxd backend status
```

A healthy local setup reports `Backend: ctxfs`, `ctxfs healthy: True`, and a
running tracker.

## Move a folder

Remove the old registration and add the new path:

```bash
ctxd folders remove Documents
ctxd folders add /new/path/to/folder --name Documents
```

This creates a new root identity. Preserving a root identity across a move is
not currently exposed by the CLI.

## Stop tracking a folder

```bash
ctxd folders remove Documents
```

This removes the tracked root from ctxd. It does not delete the source folder.

## Upgrade

Upgrade the package, then restart:

```bash
ctxd tracker stop
pip install --upgrade ctxd
ctxd tracker start
```

Existing roots and local state are preserved.

## Uninstall

```bash
ctxd tracker stop
pip uninstall ctxd
```

## Troubleshooting

If ctxfs is unreachable:

```bash
ctxd config get backend
ctxd backend status
ctxd tracker status
```

If a change has not appeared, restart tracking with `ctxd tracker stop` followed
by `ctxd tracker start`, then check `ctxd tracker status`.
