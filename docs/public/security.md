---
title: 'Security'

---

ctxd is local-first. The tracker and ctxfs service run under your user account and process only folders you explicitly add.

## Network boundary

- Local ctxfs binds to loopback by default.
- Do not bind ctxfs to a public interface. The loopback API assumes the operating-system user boundary.

## What ctxd stores

ctxfs stores its local tree and catalog under `~/.ctxd/ctxfs` by default. The tracker stores configuration, state, and logs under `~/.ctxd/local`.

Text files that need no transformation may be linked into the ctxfs tree instead of copied when the filesystem supports it. Content that requires transformation is stored as a ctxfs-owned representation. The source files remain in their original folders.

## What ctxd does not store

- Data from folders you did not add
- A semantic-search embedding index

## Per-user isolation

The service is installed per user, and its state is stored in that user's home directory. Use normal operating-system account and file permissions to control access to the tracked folders and ctxfs state.

## Removing data

Stop tracking a folder without deleting the source folder:

```bash
ctxd folders remove <ROOT_ID>
```

Remove the service and all local daemon state:

```bash
ctxd service uninstall --purge-state --clear-credentials
```

Review the paths before using the purge options. This action removes ctxd-managed local state, not your tracked source folders.
