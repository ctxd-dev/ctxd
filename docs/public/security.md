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

## Authorization scopes

Grant only the capabilities a client needs. `admin` is all-capability and must
stand alone; `enroll` is a one-use machine-bootstrap capability and also must
stand alone.

| Scope | Purpose and affected endpoints |
| --- | --- |
| `status` | Read `/health` and initialize or ping MCP; combine with `read` for normal MCP readers |
| `status:write` | Reserved for future machine-status publication; issued with a registered machine's `submit` grant but currently consumed by no endpoint |
| `read` | Read the `/api/ctxfs` stat, listing, matching, and content endpoints and call MCP tools |
| `submit` | Send tracker changes to `/api/ctxfs/submissions`, limited by server-owned prefix grants |
| `enroll` | Call `/api/ctxfs/machines/register` once to obtain a machine credential |
| `admin` | Perform every operation; reserve for local server administration and credential lifecycle work |

## What ctxd stores

ctxfs stores its local tree and catalog under `~/.ctxd/ctxfs` by default. The tracker stores configuration, state, and logs under `~/.ctxd/local`.

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

Uninstalling does not delete `~/.ctxd`. If you choose to remove that directory
manually, review the path first: it contains ctxd-managed configuration, tracker
state, and ctxfs data, but not the tracked source folders themselves.
