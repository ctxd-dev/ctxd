# Secure standalone ctxfs server

`ctxd server serve` runs a standalone ctxfs server in the foreground. With no
listener options it uses the private managed Unix socket and trusted-local
authentication:

```console
ctxd server serve --root ~/.ctxd/ctxfs
```

Choose `--socket PATH` for another trusted local socket. Unix-socket access is
an explicit local trust boundary and does not accept bearer credentials.

An explicit TCP listener always uses the credential store for bearer
authentication, including loopback listeners:

```console
ctxd server serve --host 127.0.0.1 --port 8765
```

Create credentials against the same server root with `ctxd keys`. The server
may use a separate credential database root via `--credential-root PATH`.

Serving outside loopback is deliberately fail-closed. It requires both network
opt-in and direct TLS configuration:

```console
ctxd server serve \
  --host 0.0.0.0 \
  --port 8443 \
  --allow-network \
  --tls-cert /etc/ctxd/server.crt \
  --tls-key /etc/ctxd/server.key \
  --advertised-url https://ctxfs.example.test:8443
```

The certificate and private key must be readable regular files. Wildcard,
private-network, public, and hostname binds are all treated as network binds.
`localhost`, IPv4 loopback addresses, and IPv6 `::1` are treated as loopback.
Reverse-proxy TLS is not inferred and plaintext remote binds are rejected.
Tokens are never accepted as command-line server arguments or logged.
