---
title: 'Local tracker'
---

The local tracker watches folders you select and synchronizes their ctxfs
representation when a scan completes successfully. The tracker and ctxfs server
are separate processes. Starting the
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

## Incomplete scans and stale content

A running tracker does not by itself guarantee that indexed content is current.
A file read or stat error, invalid UTF-8 content, or an unavailable registered
folder makes that folder's scan incomplete. The tracker still submits changes
for files it read successfully, but it does not infer deletions for the affected
folder from that scan. This folder-wide safeguard prevents a temporary access or
decoding failure from deleting unrelated indexed content.

Consequently, a file that was indexed earlier can remain available in ctxfs at
its last successfully indexed version even after its source changes or becomes
unreadable. The source folder remains authoritative; the retained ctxfs version
is a temporary, potentially stale representation. Restarting the tracker does
not correct it while the underlying read, stat, UTF-8, or folder-access problem
remains.

Restore access to the folder or file, or replace invalid content with valid
UTF-8, then allow a later poll to complete. That complete scan reconciles
updates and deletions. If you intentionally want to stop tracking the whole
folder, `ctxd folders remove NAME` is different: it removes the tracked root
from ctxfs rather than preserving it as an incomplete scan.

## Monitor

```bash
ctxd tracker status
ctxd endpoint status
```

A healthy setup reports the effective endpoint, `ctxfs healthy: true`, a
running tracker, and `last sync result: success`. Process liveness and
synchronization health are separate: a tracker may remain running after a sync
failure, and liveness alone does not prove that every indexed file is current.

The status distinguishes these synchronization states:

- `sync diagnostics: not-attempted`: no persisted attempt exists yet.
- `last sync result: success`: the most recent scan completed and its operations
  were submitted.
- `last sync result: failed`: the most recent attempt raised an error.
- `last sync result: incomplete`: at least one folder could not be scanned
  completely, so conservative prior tracker state may have been retained.
- `sync diagnostics: unavailable`: the health record exists but cannot be read
  or validated.

Status includes UTC epoch-second timestamps for the last attempt and last
complete success, folder and operation counts where available, and a safe error
category for failures.

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

Uninstalling the package does not remove `~/.ctxd`. Review and remove that
directory separately only if you intend to delete all ctxd-managed local state.

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

If a change has not appeared, check the last sync result first. For `failed`,
confirm endpoint availability and credentials. For `incomplete`, check that the
source folder still exists and that affected files are readable, within the size
limit, and valid UTF-8. Correct the source problem and allow another polling
interval. Restarting with `ctxd tracker stop` followed by `ctxd tracker start`
can recover a stopped process, but it cannot repair invalid source content.
