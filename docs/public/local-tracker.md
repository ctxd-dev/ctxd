---
title: 'Local tracker'
---

The local tracker watches folders you select and keeps their ctxfs representation current. It runs with the local ctxfs server as one per-user background service.

## Requirements

- A supported macOS, Linux, or Windows user environment
- [`uv`](https://docs.astral.sh/uv/) for installing the supplied package bundle
- A local folder you want ctxd to track

## Install

Obtain and unpack the local tracker bundle supplied by ctxd, then install from that directory:

```bash
uv tool install --force --find-links /path/to/local-file-daemon-dist local-file-daemon
ctxd version
```

The bundle includes the tracker and ctxfs dependencies. ctxfs is not installed as a separate system service.

## Start in ctxfs mode

```bash
ctxd config set backend ctxfs
ctxd service status
```

Selecting the `ctxfs` backend installs and starts the per-user service. The service starts again at login and restarts after failures.

## Add folders

```bash
ctxd folders add ~/Documents --name Documents
ctxd folders add ~/Projects/example --name Example
ctxd folders list
```

Each folder receives a stable `ROOT_ID`. Its ctxfs prefix is `local-files/<ROOT_ID>/`.

Run an immediate pass if you do not want to wait for the next service tick:

```bash
ctxd daemon run --once
```

## Monitor

```bash
ctxd status
ctxd service status
tail -n 100 "$HOME/.ctxd/local/logs/daemon.jsonl"
```

A healthy local setup reports `Backend: ctxfs` and `ctxfs reachable: yes`.

## Move a folder

Repair the existing root so it keeps its identity:

```bash
ctxd folders repair-move <ROOT_ID> /new/path/to/folder --name Documents
```

Do not remove and re-add a moved folder unless you intentionally want a new root identity.

## Stop tracking a folder

```bash
ctxd folders remove <ROOT_ID>
```

This removes the tracked root from ctxd. It does not delete the source folder.

## Upgrade

Install the newer bundle over the existing tool environment, then restart:

```bash
uv tool install --force --find-links /path/to/local-file-daemon-dist local-file-daemon
ctxd service restart
```

Existing roots and local state are preserved.

## Uninstall

```bash
ctxd service uninstall
uv tool uninstall local-file-daemon
```

To also remove local state and stored credentials:

```bash
ctxd service uninstall --purge-state --clear-credentials
uv tool uninstall local-file-daemon
```

## Troubleshooting

If ctxfs is unreachable:

```bash
ctxd config show
ctxd service restart
ctxd service status
```

If a change has not appeared, run `ctxd daemon run --once` and inspect the daemon log. An unconfigured API key is expected in local-only ctxfs mode.
