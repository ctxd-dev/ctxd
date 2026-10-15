# ctxfs authorization model

## Status and scope

This document defines the authorization contract for the first authenticated
ctxfs network release. The release is **single-tenant**: all principals operate
inside one server-owned context namespace, while scoped credentials control
which capabilities and writer prefixes each client may use.

This contract does not implement credential persistence, bearer-token parsing,
HTTP middleware, CLI key administration, or TLS. Those layers must consume this
policy without weakening it.

## Server-derived identity

An authenticated principal contains an opaque principal ID, an immutable key ID,
its scopes, and any submission prefix grants. All of these values come from the
server's credential record after authentication.

Fields supplied in an API payload, including `user_id`, `writer_id`, and
`writer_prefix`, are provenance or routing metadata. They never select a
principal, add a scope, or expand an allowed prefix. Before a submission reaches
storage, the server must bind it to the authenticated principal and validate
each affected path against that principal's grants.

## Scopes

| Scope | Capability |
| --- | --- |
| `status` | Read readiness, liveness, and non-sensitive service status. |
| `read` | List, search, inspect, and read indexed content through REST or MCP. |
| `submit` | Register or submit content only inside an assigned prefix. |
| `admin` | Perform every capability, including credential and server administration. |

`admin` is an all-capability scope and stands alone. Prefix grants are valid
only with `submit`. A submit credential without prefix grants cannot write.
Prefix matching is segment-aware: `teams/alpha` includes
`teams/alpha/notes.md`, but not `teams/alphabet/notes.md`.

## Local Unix-socket trust

Local socket access is controlled by an explicit server policy:

- `disabled` gives the socket no implicit identity, so normal authentication is
  still required.
- `trusted_admin` maps an authenticated local socket peer to a synthetic
  administrative principal.

The trusted policy applies only to a local Unix socket. TCP clients never inherit
it, including loopback clients. The transport layer is responsible for securely
identifying a local peer before constructing the trusted principal.

## Decisions and endpoint behavior

Policy decisions include the required scope, principal and key IDs for safe
audit correlation, and a stable denial reason. They never contain bearer-token
material.

HTTP adapters must map decisions consistently:

- missing or invalid authentication: `401 Unauthorized`;
- authenticated principal lacking a scope or prefix grant: `403 Forbidden`;
- authorized requests: continue to the endpoint, whose normal success code and
  validation errors still apply.

Malformed input remains `400`/`422`, missing content remains `404`, and conflicts
remain `409`; authentication and authorization run before disclosing protected
resource existence. Readiness/liveness routes may be public only if they expose
no sensitive server or store information. Rich status requires `status`.

## REST and MCP parity

REST and HTTP MCP use the same authenticated principal and authorization policy.
MCP tool discovery requires `read` and exposes only tools permitted for that
connection. Every MCP tool invocation is authorized again; discovery is not a
lasting grant. Equivalent REST and MCP operations must produce equivalent allow
or deny decisions, including prefix checks and `401`/`403` behavior.

Stdio MCP is local process transport, not a network authentication bypass. It
may use the explicit trusted-local policy or a configured credential, according
to how the server process is launched.

## Stability boundary

The policy types and decisions are reusable domain primitives. Credential
storage will produce their inputs, and REST/MCP middleware will enforce their
outputs. A later multi-tenant release will require a separate design for tenant
identity and storage isolation rather than treating request-provided user IDs as
an authorization boundary.
