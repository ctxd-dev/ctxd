# ctxd

<p align="center">
  <img src="img/ctxd.png" alt="ctxd logo" width="160">
</p>

Python SDK and CLI for the `ctxd` platform.

## Overview

CTXD is a local-first context tool for AI agents. The `ctxd` command can act as
a client CLI, start a local ctxfs service, and optionally run a local tracker
daemon that syncs folders into a searchable local ctxfs store.

For a fuller introduction, see the [CTXD docs overview](https://ctxd.dev/docs/overview/).

## Install

```bash
pip install ctxd
```

The package provides:

- the `ctxd` CLI
- the sync `Client`
- the async `AsyncClient`

## Choose a Backend

### Local ctxfs

Use the local backend when you want ctxd to read from a local ctxfs store:

```bash
ctxd backend set ctxfs
ctxd backend status
```

Selecting `ctxfs` starts the local ctxfs service if it is not already running.
It does not start the local tracker daemon.

After that, query and read commands use the local backend by default:

```bash
ctxd search "deployment"
ctxd files tree
```

### Remote API

Use the remote backend when you want the client to call the hosted API:

```bash
ctxd backend set remote
ctxd login
ctxd search "deployment"
```

For scripts or headless use, set `CTXD_API_KEY` instead of running
`ctxd login`:

```bash
export CTXD_API_KEY=...
ctxd backend set remote
ctxd search "deployment"
```

## Track a Local Folder

Folder tracking is optional and explicit. Add a folder, then start the tracker:

```bash
ctxd folders add ~/Documents --name Documents
ctxd tracker start
ctxd tracker status
```

The tracker syncs accepted files from configured folders into the local ctxfs
service. It is separate from `ctxd backend set ctxfs`, so choosing the local
backend does not begin folder syncing by itself.

## Query and Read Synced Content

Once a folder has been tracked, use the folder name in search and read commands:

```bash
ctxd search "deployment" --folder Documents
ctxd files tree Documents --depth 2
ctxd files read Documents/README.md
ctxd files read-lines Documents/README.md --start 1 --end 20
```

Endpoint discovery uses `CTXD_CTXFS_URL`, `CTXD_CTXFS_SOCKET`, stored config, the
local daemon config, `unix://~/.ctxd/local/ctxfs.sock` when present, then
`http://127.0.0.1:8765`. Local ctxfs reads do not use `CTXD_API_KEY`.

## SDK Example

SDK callers can select the same backend. Folder names such as `Documents` are a
CLI convenience; SDK file methods use ctxfs paths returned by search or tree
results.

```python
from ctxd import Client

client = Client(backend="ctxfs")
matches = client.search("deployment")
if matches.results:
    document = client.fetch(matches.results[0].url)
```

For remote API usage, pass an API key or set `CTXD_API_KEY`:

```python
from ctxd import Client

client = Client(api_key="<api-key>", backend="remote")
results = client.search("deployment")
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the package version bump and release
process.
