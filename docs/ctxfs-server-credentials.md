# ctxfs server credentials

ctxfs keeps API-key metadata and verifiers in the server data root. This is a
local administrator interface; it does not yet enable HTTP authentication or
make network deployment supported.

Create a read credential in the managed server data root:

```console
ctxd keys create --name laptop-reader --scope read
```

Create a submission credential restricted to one ctxfs prefix:

```console
ctxd keys create \
  --name workstation-tracker \
  --scope submit \
  --prefix Workstation
```

The bearer token is printed exactly once. Save it in an appropriate secret
manager. `ctxd keys list` displays only metadata and never recovers a token.

```console
ctxd keys list
ctxd keys rotate KEY_ID
ctxd keys revoke KEY_ID
```

Rotation creates a new immutable key ID and token, then immediately revokes the
old key. To administer another server data root locally, place `--root PATH`
before the subcommand, for example `ctxd keys --root /srv/ctxfs create ...`.

The credentials database and its directory are owner-only. The database stores
a salted scrypt verifier rather than the token itself. Creating, rotating, or
revoking keys requires direct local access to the server data root; tokens are
never accepted as command-line arguments.
