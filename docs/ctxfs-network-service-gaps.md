# ctxfs network service gaps

## Purpose

This document describes the gap between the current local-first ctxfs runtime and
the intended model in which one ctxfs server can safely store context submitted
by multiple tracker clients and serve it to ctxd clients over a network.

The target does not require a tracker or a tracked folder on the server. A server
may begin with an empty ctxfs store, accept content from authorized writers, and
serve that content to authorized readers.

## Current implementation

### Storage and reads

- ctxfs stores indexed text and its metadata in a server-side store.
- The filesystem-shaped tree is the namespace through which clients list,
  search, inspect, and read that stored content; it is not merely a directory
  listing detached from the content.
- The ctxd client and CLI can connect to ctxfs through a Unix socket or an HTTP
  URL. `CTXD_CTXFS_URL` selects an HTTP endpoint.
- The `ctxd files ls`, `tree`, `glob`, `stat`, `read`, `read-lines`, and `grep`
  commands use the configured ctxfs endpoint, so the endpoint can mechanically
  be local or remote.

### Ingestion

- A tracker scans configured folders and submits operations to the resolved ctxfs
  endpoint.
- Running ctxfs does not intrinsically require any configured folders.
- The submission endpoint accepts writer identity and prefix information from
  the request and stores all data under the single local service identity.
- `ctxd tracker start` starts or verifies ctxfs only for the default managed
  local socket. When an endpoint is explicitly configured, it verifies that
  endpoint without starting another server.

### Authentication and network boundary

- The ctxfs REST and MCP endpoints do not authenticate requests.
- The ctxfs client deliberately does not resolve or send an API key.
- The server does not distinguish authenticated users, readers, writers, or
  administrators.
- Writer identifiers and prefixes are not authorization credentials. A remote
  caller can supply them in a submission request.
- The managed service defaults to a private Unix socket. The fallback TCP server
  binds to loopback, which is the only safe boundary for the current API.
- The existing ctxd API-key flow belongs to the hosted/remote backend. It does
  not protect ctxfs HTTP or MCP endpoints.

Consequently, configuring an HTTP ctxfs URL is transport support, not a secure
multi-client network deployment. The current server must not be exposed to an
untrusted network.

## Target model

The intended topology has three independently deployable roles:

1. **ctxfs server** — owns the indexed content and metadata, exposes REST and
   MCP, and may run without a local tracker or tracked folders.
2. **tracker/writer client** — watches explicitly configured folders and submits
   bounded changes to an authorized namespace on a ctxfs server.
3. **reader client** — uses the ctxd CLI, SDK, REST API, or MCP to search and read
   authorized content from the configured ctxfs server.

A single machine may run all three roles, but the implementation and
documentation must not assume that they are colocated.

## Required work

### 1. Server lifecycle and topology

- Add an explicit server command and configuration model that can run ctxfs
  independently of the tracker.
- Support deliberate Unix-socket, loopback TCP, and network TCP modes.
- Require an explicit opt-in to bind beyond loopback.
- Make the data root, listen address, advertised URL, TLS configuration, and
  authentication policy visible in status output.
- Define process supervision and shutdown behavior for foreground, systemd,
  launchd, and container deployments.
- Add readiness and liveness endpoints that do not disclose store contents.

### 2. Tracker behavior

- Change `ctxd tracker start` to resolve the effective ctxfs endpoint first.
- Start the managed local ctxfs process only when the effective endpoint is the
  managed local service.
- When an HTTP or externally managed socket endpoint is configured, verify that
  endpoint instead of starting a second local server.
- Permit a ctxfs server to run normally with zero configured folders.
- Treat `ctxd folders add` as an optional writer configuration step, not a
  prerequisite for starting or reading ctxfs.
- Add retry, backoff, idempotency, and observable delivery state for remote
  submissions.

### 3. Identity and API-key lifecycle

Add a server-owned credential lifecycle. A proposed operator flow is:

```text
ctxd keys create --name laptop-reader --scope read
ctxd keys create --name workstation-tracker --scope submit --prefix Workstation
ctxd keys list
ctxd keys revoke <key-id>
```

Key creation must:

- generate at least 256 bits of cryptographically secure random material;
- display the bearer token only once;
- store a salted password hash or keyed verifier, never the plaintext token;
- assign an immutable key ID, display name, creation time, scopes, and optional
  expiry;
- support rotation and immediate revocation;
- avoid writing tokens to logs, process arguments, shell history, or status
  output.

For an initial single-server deployment, local server administrators may create
keys directly. A later control plane could issue credentials, but ctxfs must not
depend on a hosted service to authenticate a private deployment.

### 4. Client authentication

- Allow credentials to be configured per ctxfs endpoint rather than globally.
- Send `Authorization: Bearer <token>` for ctxfs REST and HTTP MCP requests.
- Define authentication for streaming MCP connections if introduced.
- Provide an interactive client flow that associates a token with an endpoint,
  for example:

  ```text
  ctxd backend set ctxfs --endpoint https://ctxfs.example.net
  ctxd login --endpoint https://ctxfs.example.net
  ```

- Continue to support environment-based credentials for automation without
  persisting them.
- Keep Unix-socket peer trust as an explicit local policy; do not silently carry
  that no-auth policy over to TCP.

### 5. Authorization

At minimum, define separate capabilities for:

- health/status;
- read operations;
- MCP tool discovery and calls;
- content submission;
- credential and server administration.

Writer credentials must be restricted to assigned prefixes. The server must
derive the principal and allowed prefixes from the authenticated credential,
not from request-provided `user_id`, `writer_id`, or `writer_prefix` values.

Decide whether the first network release is:

- single-tenant with multiple scoped credentials; or
- multi-tenant with an authenticated tenant/user identity included in every
  storage operation.

This decision must be explicit before the API is declared stable. The current
hard-coded `local` identity is not a multi-tenant boundary.

### 6. Transport security

- Require TLS for non-loopback TCP endpoints.
- Document certificate provisioning and trust for public certificates, private
  certificate authorities, and development environments.
- Reject or prominently warn about plaintext remote endpoints.
- Define reverse-proxy support, trusted proxy headers, request-size limits,
  timeouts, and rate limits.
- Apply authentication and authorization consistently to REST and MCP.

### 7. Namespace and writer semantics

- Define how server administrators grant and transfer prefix ownership.
- Prevent one writer from registering or overwriting another writer's prefix.
- Specify collision behavior when multiple clients propose the same path.
- Preserve idempotency across retries using stable writer IDs, run IDs, sequence
  numbers, source keys, and source versions.
- Define server behavior when a writer is revoked, disappears, or requests a
  full-scan deletion.
- Expose provenance sufficient to identify which authorized writer produced a
  path without exposing secrets.

### 8. CLI and configuration

The CLI needs a coherent distinction between:

- the hosted `remote` backend, if it remains supported;
- a managed local ctxfs service;
- an externally managed ctxfs Unix socket; and
- a remote ctxfs HTTP service.

Required commands or equivalents include:

- configure and inspect the effective ctxfs endpoint;
- start and stop only locally managed processes;
- start a standalone server;
- create, list, rotate, and revoke server credentials;
- authenticate a reader or writer client to a specific endpoint;
- show connectivity, authentication, authorization, and tracker delivery status
  without disclosing credentials.

`ctxd backend status` must report the effective configured endpoint. It must not
report the default local process as unavailable when the client is intentionally
using a healthy remote server.

### 9. Observability and operations

- Emit structured audit events for key creation, revocation, authentication
  failures, denied operations, submissions, and administrative changes.
- Include principal/key IDs but never bearer tokens in logs.
- Expose metrics for request counts, latency, failures, active writers,
  submission lag, rejected prefixes, and store health.
- Provide backup, restore, migration, and data-retention procedures.
- Define limits for files, submissions, operations, response sizes, and stored
  content.

### 10. Tests

Add coverage for:

- a server running and serving an empty store with no tracker;
- a tracker using a remote endpoint without starting local ctxfs;
- multiple authorized writers with disjoint prefixes;
- reader-only, writer-only, administrator, expired, and revoked credentials;
- missing, malformed, and leaked credentials;
- cross-prefix and cross-tenant denial;
- REST and MCP authorization parity;
- TLS verification and rejection of unsafe remote defaults;
- retries and duplicate submissions without duplicated or corrupted state;
- credential rotation without service interruption;
- CLI reads over Unix sockets, loopback HTTP, and authenticated network HTTP;
- status output for managed-local and external-remote deployments.

The end-to-end network suite should use separate server, writer, and reader
processes so an accidental shared filesystem or environment cannot make the test
pass.

## Documentation changes after implementation

Once the network boundary is implemented, public docs should say:

- adding a folder is optional and applies to tracker/writer clients;
- ctxfs stores indexed content and exposes it through filesystem-shaped paths;
- `ctxd files ...` reads from the configured ctxfs endpoint, whether local or
  remote;
- starting a tracker starts local ctxfs only in managed-local mode;
- remote deployments require HTTPS and scoped credentials;
- how a server administrator creates a key and how a client installs, rotates,
  and removes it.

Until then, public documentation should describe the supported managed-local
workflow and avoid implying that unauthenticated ctxfs HTTP is safe for network
clients.

## Suggested delivery sequence

1. Separate managed-local process startup from endpoint selection. **Implemented
   in [PR #34](https://github.com/ctxd-dev/ctxd/pull/34); independent review and
   targeted local verification passed; CI triggered.**
2. Define the single-tenant principal, scope, and prefix authorization model.
   **Implemented in [PR #35](https://github.com/ctxd-dev/ctxd/pull/35);
   independent review and targeted local verification passed; CI triggered.
   Runtime credential and middleware integration remain in later items.**
3. Implement server-side credential storage and administration. **Implemented
   in [PR #36](https://github.com/ctxd-dev/ctxd/pull/36); independent review and
   targeted local verification passed; CI triggered. HTTP middleware and client
   credential transport remain in later items.**
4. Add REST and MCP authentication/authorization middleware. **Implemented in
   [PR #37](https://github.com/ctxd-dev/ctxd/pull/37); independent review and
   targeted local verification passed; CI triggered. Managed Unix sockets use
   an explicit trusted-local principal, while HTTP requires server-verified
   bearer credentials. Client credential transport and supported TLS serving
   remain in later items.**
5. Add endpoint-scoped client credentials and authenticated CLI/SDK transport.
   **Implemented in [PR #38](https://github.com/ctxd-dev/ctxd/pull/38);
   independent review and targeted local verification passed; CI triggered.
   HTTP endpoints resolve isolated credentials by normalized endpoint, while
   trusted Unix sockets deliberately omit bearer credentials.**
6. Add explicit network serving with TLS requirements and safe defaults.
7. Add multi-process end-to-end, abuse, rotation, and recovery tests.
8. Update public docs and declare the authenticated network topology supported.

Network deployment should not be documented as supported until steps 1 through
7 are complete and verified.
