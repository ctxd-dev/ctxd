---
title: 'Local tracker'
---

The local tracker watches folders you select and keeps their ctxfs representation
current. The tracker and ctxfs server are separate processes. Starting the
tracker starts ctxfs only when the effective endpoint is the default managed
local socket; an explicitly configured endpoint is checked instead.

## Requirements

- macOS or Linux
- Python 3.11 or 3.12 with `pip`
- A local folder if this machine will contribute content

## Install

Install ctxd from PyPI:

```bash
pip install ctxd
ctxd --version
```

The package includes the CLI, tracker, and ctxfs. ctxfs is not installed as a separate system service.

## Start in ctxfs mode

```bash
ctxd backend set ctxfs
ctxd tracker start
ctxd backend status
```

With the default endpoint, selecting the backend starts ctxfs. `tracker start`
launches the tracker as a detached process for the current user. Both commands
check an explicitly configured endpoint without starting another server. For
example, configure and authenticate to a network server before starting:

```bash
ctxd login --endpoint https://ctxfs.example.net:8443
ctxd backend set ctxfs --endpoint https://ctxfs.example.net:8443
ctxd tracker start
```

The tracker uses a `submit` credential whose prefix grant covers the submitted
ctxfs paths. Start the tracker again after a reboot; automatic login
registration is not currently installed.

The tracker can run with zero configured folders. Add folders only on machines
that should contribute local content.

## Add folders

```bash
ctxd folders add ~/Documents --name Documents
ctxd folders add ~/Projects/example --name Example
ctxd folders list
```

Each folder name becomes a CLI path alias, such as `Documents/notes.md`. The
underlying ctxfs prefix has a generated root ID, but ordinary CLI use does not
require it.

## Monitor

```bash
ctxd tracker status
ctxd backend status
```

A healthy setup reports `Backend: ctxfs`, the effective endpoint, `ctxfs
healthy: True`, and a running tracker. For an external endpoint, ctxd reports
that the server is not locally managed.

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

Configured folders and local ctxfs state are preserved.

## Uninstall

```bash
ctxd tracker stop
pip uninstall ctxd
```

Uninstalling the package does not remove `~/.ctxd`. Review and remove that
directory separately only if you intend to delete all ctxd-managed local state.

## Troubleshooting

If ctxfs is unreachable or rejects authentication:

```bash
ctxd config get backend
ctxd backend status
ctxd tracker status
```

For an HTTP endpoint, confirm `ctxd status --endpoint URL`, the server
certificate trust chain, and that the credential has `submit` plus the required
prefix. Rotation revokes the old key immediately; run `ctxd logout --endpoint
URL`, then `ctxd login --endpoint URL` and enter the newly printed token.

If a change has not appeared, restart tracking with `ctxd tracker stop` followed
by `ctxd tracker start`, then check `ctxd tracker status`.
