---
title: 'Security and data'

---

ctxd supports a managed local deployment and an authenticated ctxfs network
deployment. A tracker processes only folders explicitly added on that client;
a server can run without any local folders.

## Network boundary

- The managed Unix socket uses local peer trust and private filesystem
  permissions.
- TCP always requires a bearer credential, including loopback.
- Non-loopback serving additionally requires `--allow-network` and direct TLS.
- Clients must verify a certificate valid for the configured endpoint hostname.
- Do not expose server state, private keys, or bearer tokens through shared
  files, logs, command arguments, or URLs.

This is a single ctxfs namespace with scoped credentials, not a multi-tenant
security boundary. `read`, `submit`, `status`, and `admin` are distinct scopes;
submission credentials are restricted by server-owned prefix grants. REST and
MCP enforce the same policy.

## What ctxd stores

The managed local deployment uses these default paths:

| Data | Default location | Lifecycle |
| --- | --- | --- |
| CLI settings, registered source paths, and generated ctxfs prefixes | `~/.ctxd/config.json` | Persistent |
| Client bearer credentials and machine registration state | `~/.ctxd/credentials.json` | Persistent and sensitive |
| Tracker synchronization metadata | `~/.ctxd/local/tracker-state.json` | Persistent and sensitive |
| Tracker process ID | `~/.ctxd/local/tracker.pid` | Runtime only |
| Managed ctxfs process ID, socket, and lock | `~/.ctxd/local/ctxfs.pid`, `~/.ctxd/local/ctxfs.sock`, and `~/.ctxd/local/ctxfs.lock` | Runtime only |
| Managed ctxfs tree and catalog | `~/.ctxd/ctxfs/` | Persistent and sensitive |

The detached tracker does not currently create a log file. Use `ctxd tracker
status` and `ctxd endpoint status` for the available health information.

`CTXD_CONFIG_PATH` selects a different configuration file.
`CTXD_CREDENTIALS_PATH` independently selects a different credentials file and
takes precedence for that file. If only `CTXD_CONFIG_PATH` is set, the
credentials file defaults to `credentials.json` beside the selected
configuration file. Changing either variable does not move existing data.

The credentials file contains bearer credentials. The configuration file
reveals registered source paths, the tracker state contains synchronization
metadata, and the ctxfs data root contains indexed representations of tracked
content. ctxd creates managed files and directories with user-only permissions
where supported. These permissions are not encryption and do not protect state
from other processes running as the same operating-system account or from
operating-system administrators.

Text files that need no transformation may be linked into the ctxfs tree instead of copied when the filesystem supports it. Content that requires transformation is stored as a ctxfs-owned representation. The source files remain in their original folders.

## What ctxd does not store

- Data from folders you did not add
- A semantic-search embedding index

## Per-user isolation

The managed local ctxfs service runs under the invoking operating-system account
and protects its default runtime and data directories with user-only
permissions. It relies on normal operating-system account isolation and is not
a separate security sandbox. Other processes running as the same account, and
operating-system administrators, can access ctxd state.

For a standalone server, protect its selected `--root`, credential database,
TLS private key, and backups with a dedicated operating-system account and
private permissions. ctxd does not configure a service manager, reverse proxy,
firewall, certificate issuer, backup system, or multi-tenant isolation.

## Credential recovery

Tokens cannot be recovered from the server: it stores verifiers, not plaintext.
Create a replacement credential if a token is lost. If a token may be exposed,
revoke it immediately with `ctxd keys --root ROOT revoke KEY_ID`, create a
replacement, and install the new token with `ctxd logout --endpoint URL`
followed by `ctxd login --endpoint URL`. Credential recovery requires local
filesystem access to the server data root.

## Removing data

Stop tracking a folder without deleting the source folder:

```bash
ctxd folders remove Documents
```

Stop tracking and uninstall the package:

```bash
ctxd tracker stop
pip uninstall ctxd
```

`ctxd tracker stop` stops the tracker and removes its runtime process-ID file;
it preserves configuration, credentials, synchronization state, indexed ctxfs
data, and the original source folders. Uninstalling the package also does not
delete `~/.ctxd` or a path selected by an environment override.

If you choose to remove `~/.ctxd` manually, stop ctxd processes and review the
path first. Removing it deletes the default ctxd-managed configuration,
credentials, tracker state, runtime files, and indexed ctxfs data. It does not
delete the original tracked source folders. Data stored at paths selected by
`CTXD_CONFIG_PATH` or `CTXD_CREDENTIALS_PATH` must be reviewed separately.
