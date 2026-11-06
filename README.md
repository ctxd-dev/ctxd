# ctxd

<p align="center">
  <img src="img/ctxd.png" alt="ctxd logo" width="160">
</p>

Local context infrastructure for AI agents.

## Overview

ctxd tracks folders selected on your computer and exposes them through ctxfs as
path-oriented, agent-readable context.

The user documentation in [`docs/public`](docs/public/README.md) is the source of
truth for [ctxd.dev/docs](https://ctxd.dev/docs/overview/):

- [Overview](docs/public/overview.md)
- [Quickstart](docs/public/quickstart.md)
- [Local tracker](docs/public/local-tracker.md)
- [ctxfs](docs/public/ctxfs.md)
- [CLI](docs/public/cli.md)
- [Local API](docs/public/api.md)
- [Security](docs/public/security.md)

See the canonical pages for full setup and reference instructions.

## Model Context Protocol (MCP)

The package includes [`ctxd-mcp`](src/ctxd/mcp_server.py), a local MCP server
that lets agents inspect paths and read synchronized source content over standard
input/output (stdio).

For the PyPI package, run in your Python environment on your Linux workstation
as your normal user. Use Python 3.11 or 3.12 with the system `ps` command available:

```bash
pip install ctxd
ctxd backend set ctxfs
ctxd folders add /path/to/your/source --name Source
python -m ctxd.tracker_service sync-once
ctxd tracker start
```

Replace `/path/to/your/source` with the folder to track. With `uv` installed,
add this configuration to your MCP client running as the same user:

```json
{
  "mcpServers": {
    "ctxd": {
      "command": "uvx",
      "args": ["--python", "3.12", "--from", "ctxd", "ctxd-mcp"]
    }
  }
}
```

Use `tree` to find a source path, then pass that path to `read` or `read_lines`.
After editing a source, synchronize it and read it again. The tracker runs in
the background; `python -m ctxd.tracker_service sync-once` also synchronizes on
demand. No hosted account or API key is needed for this local setup.

The published 0.1.16 package supports `ls`, `tree`, `glob`, `stat`, `read` and
`read_lines` through MCP. Its advertised `grep` tool fails; use the CLI or Python
for exact text search. See the [MCP reference](docs/public/ctxfs.md#mcp) for the
current source's endpoint configuration.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the package version bump and release
process.
