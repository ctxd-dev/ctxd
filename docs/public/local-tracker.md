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

The package includes the CLI, tracker, and ctxfs.

## Start tracking

```bash
ctxd tracker start
ctxd endpoint status
```

`tracker start` starts ctxfs at the default local endpoint and launches the
tracker as a detached process for the current user. It checks an explicitly
configured endpoint without starting another server. For
example, configure and authenticate to a network server before starting:

```bash
ctxd login --endpoint https://ctxfs.example.net:8443
ctxd endpoint set https://ctxfs.example.net:8443
ctxd tracker start
```

The tracker uses a `submit` credential whose prefix grant covers the submitted
ctxfs paths. The tracker does not start automatically when you sign in or
restart the machine. Run `ctxd tracker start` after each reboot.

## Add folders

```bash
ctxd folders add ~/Documents --name Documents
ctxd folders add ~/Projects/example --name Example
ctxd folders list
```

Each folder name becomes a CLI path alias, such as `Documents/notes.md`. The
underlying ctxfs prefix has a generated root ID, but ordinary CLI use does not
require it.

## What gets tracked

The tracker indexes text files that meet all of these conditions:

- The file is valid UTF-8 text.
- Its size is at most 5,000,000 bytes.
- Its filename does not start with `.`.
- Its extension is one of: `.cfg`, `.css`, `.csv`, `.html`, `.ini`, `.json`,
  `.log`, `.md`, `.py`, `.rst`, `.toml`, `.ts`, `.tsx`, `.txt`, `.yaml`, or
  `.yml`.

Other files are not added to ctxfs even though their parent folder is
registered. In particular, ctxfs does not expose arbitrary binary files,
extensionless files, files with unsupported extensions, or files larger than
the size limit. The source folder remains authoritative: the tracker reads it
and synchronizes changes to ctxfs without modifying the source files.

The tracker polls registered folders every 10 seconds rather than subscribing
to operating-system filesystem events. Consequently, a change may take up to
one polling interval, plus processing time, to appear in ctxfs.

## Monitor

```bash
ctxd tracker status
ctxd endpoint status
```

A healthy setup reports the effective endpoint, `ctxfs healthy: true`, and a
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

## Uninstall

```bash
ctxd tracker stop
pip uninstall ctxd
```

Stopping the tracker and uninstalling the package preserve ctxd-managed local
state and the original source folders. See [Security and data](/docs/security)
for the storage layout, environment overrides, and manual cleanup behavior.

## Troubleshooting

If ctxfs is unreachable or rejects authentication:

```bash
ctxd endpoint status
ctxd tracker status
```

For an HTTP endpoint, confirm `ctxd status --endpoint URL`, the server
certificate trust chain, and that the credential has `submit` plus the required
prefix. Rotation revokes the old key immediately; run `ctxd logout --endpoint
URL`, then `ctxd login --endpoint URL` and enter the newly printed token.

If a change has not appeared, restart tracking with `ctxd tracker stop` followed
by `ctxd tracker start`, then check `ctxd tracker status`.
