---
title: 'CLI'

---

The `ctxd` CLI manages the local tracker and ctxfs service and can connect to an
externally managed ctxfs server.

## Command coverage policy

`ctxd --help` and `ctxd <command> --help` are the exhaustive command and option
reference. This guide documents workflows and behavior-critical options rather
than repeating every flag. The public surface contract classifies every command
and option so additions and removals require an explicit documentation decision.

## Install

```bash
pip install ctxd
ctxd --version
```

## Configuration

```bash
ctxd endpoint status
```

The command starts ctxfs for the default local socket, or checks the configured
endpoint. The Unix socket does not require an API key. HTTP endpoints do.

Configure and authenticate to a network server:

```bash
ctxd login --endpoint https://ctxfs.example.net:8443
ctxd endpoint set https://ctxfs.example.net:8443
ctxd status --endpoint https://ctxfs.example.net:8443
ctxd endpoint status
```

`login` prompts for the token and stores it for that normalized endpoint. Use
`CTXD_CTXFS_API_KEY` instead for non-interactive use. Remove the stored token
with `ctxd logout --endpoint https://ctxfs.example.net:8443`.

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

The tracker synchronizes configured folders while it is running. It may run
with zero folders; adding folders is optional unless this machine contributes
content.

## Machine identity

Machine enrollment is optional for the default local Unix-socket workflow. Use
it when a network ctxfs administrator gives the machine a one-use enrollment
token:

```bash
CTXD_CTXFS_ENROLLMENT_TOKEN=... ctxd machine register \
  --endpoint https://ctxfs.example.net:8443 --name laptop
ctxd machine status
ctxd machine configure --description "Build laptop"
```

Registration exchanges the enrollment token for machine credentials stored in
the credentials file. `machine configure` changes local metadata only. Until a
future server metadata-update operation exists, `ctxd machine status` reports
that changed registered metadata is pending a server update. Do not put
enrollment tokens in shell history; interactive registration prompts without
echo.

## Files

Use a configured folder name as the first path segment:

```bash
ctxd files tree Documents --depth 2 --limit 100
ctxd files ls Documents
ctxd files glob "Documents/**/*.md" --limit 100
ctxd files stat Documents/notes.md
ctxd files read Documents/notes.md --max-bytes 100000
ctxd files read-lines Documents/notes.md --start 20 --end 40
ctxd files grep "deployment" --prefix Documents --limit 20
```

Every listing and matching command is bounded. Inspect `complete` and
`stopped_by` in JSON output before treating a result as exhaustive.
For grep, inspect `continuation_required` and `continuation_hint`, pass
`next_cursor` back with `--cursor` to continue the same pattern and prefix, and use
`--timeout-seconds` to override the 2-second search deadline (0.1 to 60).

## ctxfs

`ctxd endpoint status` starts the default local ctxfs process when needed and
reports the effective endpoint and health.

Run a standalone server in the foreground:

```bash
# Trusted local Unix socket
ctxd server serve --root ~/.ctxd/ctxfs --socket ~/.ctxd/local/ctxfs.sock

# Authenticated loopback HTTP
ctxd server serve --root ~/.ctxd/ctxfs --host 127.0.0.1 --port 8765

# Authenticated network HTTPS
ctxd server serve --root /srv/ctxfs --host 0.0.0.0 --port 8443 \
  --allow-network --tls-cert /etc/ctxfs/server.crt \
  --tls-key /etc/ctxfs/server.key
```

TCP uses bearer authentication. A non-loopback bind is rejected unless
`--allow-network`, `--tls-cert`, and `--tls-key` are all supplied. These are
foreground commands; ctxd does not install a background supervisor.

## Server credentials

Credential commands operate directly on a server data root:

```bash
ctxd keys --root /srv/ctxfs create --name reader --scope read --scope status
ctxd keys --root /srv/ctxfs create --name laptop --scope submit --scope status \
  --prefix local-files/ROOT_ID
ctxd keys --root /srv/ctxfs list
ctxd keys --root /srv/ctxfs rotate KEY_ID
ctxd keys --root /srv/ctxfs revoke KEY_ID
```

Creation and rotation print a bearer token once. Transfer it securely; only a
verifier is retained. `admin` grants all capabilities and cannot be combined
with other scopes. Prefix grants apply to `submit` credentials; grant each
`local-files/ROOT_ID` stored as the folder's `prefix` in the writer client's
`~/.ctxd/config.json` that the tracker may update. If `CTXD_CONFIG_PATH` is set,
inspect that file instead. `ctxd folders list` prints names and source paths,
not generated prefixes.

## MCP

For the default Unix-socket installation, configure MCP clients to launch the
stdio bridge:

```text
ctxd-mcp
```

The ctxfs server also accepts MCP JSON-RPC requests over HTTP at `POST /mcp` on
its configured endpoint.

## Environment

| Variable | Purpose |
| --- | --- |
| `CTXD_CONFIG_PATH` | Override the CLI configuration file |
| `CTXD_CREDENTIALS_PATH` | Override the sensitive credential store; changing it does not migrate existing credentials |
| `CTXD_CTXFS_SOCKET` | Use ctxfs through the selected Unix socket |
| `CTXD_CTXFS_URL` | Use ctxfs through the selected HTTP URL |
| `CTXD_CTXFS_API_KEY` | Supply a ctxfs HTTP bearer token without storing it |
| `CTXD_CTXFS_ENROLLMENT_TOKEN` | Supply a one-use machine enrollment token without prompting |
