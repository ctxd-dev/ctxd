# Local ctxfs Backend UX Plan

## Goal

`ctxd` should be the public CLI for the whole user journey:

1. install the `ctxd` CLI
2. choose a backend
3. optionally run the local tracker daemon and add folders
4. query or read synced content

The CLI should feel like one product surface. Users should not need to know
whether a command is implemented by hosted ctxd, local ctxfs, or a local tracker
daemon unless that distinction affects behavior.

## Target User Flow

### Remote Backend

```bash
pip install ctxd

ctxd backend set remote
ctxd login
ctxd search "deployment"
ctxd fetch doc-123
```

Behavior:

- no local service is started
- commands interact with hosted ctxd APIs
- hosted authentication is required

### Local ctxfs Backend

```bash
pip install ctxd

ctxd backend set ctxfs
ctxd search "invoice"
ctxd files tree
ctxd files read Documents/report.md
```

Behavior:

- selecting `ctxfs` installs or starts the local ctxfs service if needed
- selecting `ctxfs` does not start the local tracker daemon
- query and read commands use local ctxfs by default after backend selection
- users do not need to pass `--backend ctxfs` on every command

### Optional Local Tracking

```bash
ctxd folders add ~/Documents --name Documents
ctxd tracker start
ctxd tracker status
ctxd search "invoice" --folder Documents
```

Behavior:

- folder tracking is explicit
- the tracker daemon is started only when the user asks for tracking
- folders can be addressed by user-friendly names instead of raw ctxfs prefixes

## Product Model

`ctxd` should own three public concepts:

- **Backend**: where query/read commands go.
- **ctxfs service**: local read/query surface.
- **tracker daemon**: optional background sync from local folders into ctxfs.

The ctxfs service and tracker daemon are separate:

- `ctxd backend set ctxfs` should make local reads available.
- `ctxd tracker start` should start background folder sync.

This separation matters because a user may want to read an existing local ctxfs
store without enabling folder watching.

## CLI Surface

### Backend Commands

```bash
ctxd backend get
ctxd backend set remote
ctxd backend set ctxfs
ctxd backend status
```

`ctxd backend set ctxfs` should:

- persist `ctxfs` as the default backend
- install/start the local ctxfs service if needed
- print the local endpoint and service status
- not start the tracker daemon

`ctxd backend set remote` should:

- persist `remote` as the default backend
- not start or stop local services
- keep hosted auth independent from local ctxfs config

### Tracker Commands

```bash
ctxd tracker start
ctxd tracker stop
ctxd tracker status
```

Tracker commands control the local folder-sync daemon only. They should not be
required for users who only want to query an existing ctxfs store.

### Folder Commands

```bash
ctxd folders add ~/Documents --name Documents
ctxd folders list
ctxd folders remove Documents
```

Folder commands should hide raw ctxfs root IDs in normal usage. Advanced commands
may still expose raw paths when needed.

### Query and Read Commands

```bash
ctxd search "invoice"
ctxd fetch <result-id>
ctxd files tree
ctxd files read Documents/report.md
ctxd files read-lines Documents/report.md --start 1 --end 20
```

Command behavior depends on the selected backend:

- `remote`: hosted semantic search/fetch/profile
- `ctxfs`: local exact grep/read/tree operations

`--backend` remains useful as a per-command override, but it should not be part
of the normal happy path.

## Repository Direction

ctxfs should move into the public `ctxd` repo if it is intended to be open
source and distributed as part of the local user journey.

Recommended public repo ownership:

- `ctxd` CLI
- backend selection/config
- ctxfs runtime/store/read service
- ctxfs service management
- local tracker control surface
- reusable local tracker pieces that can be open sourced

Recommended private monorepo ownership:

- hosted backend implementation
- private deployment wiring
- private integration-specific server code
- secrets and production infrastructure

## Implementation Plan

### PR 1: Backend UX

- Add `ctxd backend get/set/status`.
- Keep `--backend` as an override.
- Make stored backend config the normal path.
- Preserve hosted auth behavior for `remote`.

### PR 2: Local ctxfs Service Management

- Add local ctxfs service install/start/status support to the public CLI.
- Make `ctxd backend set ctxfs` ensure ctxfs is running.
- Do not start the tracker daemon from backend selection.

### PR 3: Folder Management

- Add `ctxd folders add/list/remove`.
- Store named folder roots.
- Map folder names to ctxfs paths for query/read commands.

### PR 4: Tracker Daemon Controls

- Add `ctxd tracker start/stop/status`.
- Keep tracker lifecycle explicit and separate from ctxfs service lifecycle.
- Ensure folder registration works before the tracker is running.

### PR 5: Query/Read Polish

- Support `--folder <name>` on search.
- Support named folder paths in `ctxd files` commands.
- Keep raw ctxfs prefixes available for advanced/debug usage.

### PR 6: ctxfs Public Migration

- Move or mirror open-sourceable ctxfs runtime code into the `ctxd` repo.
- Keep private hosted/backend-only code in the monorepo.
- Update packaging so one `ctxd` install can provide the CLI and local ctxfs
  service runtime.

## Open Questions

- Should `ctxd backend set ctxfs` install a persistent OS service immediately,
  or start a user-session service first and offer install as a follow-up?
- Which tracker components are safe and useful to open source?
- Should local ctxfs search remain exact grep only, or should the CLI label it
  explicitly as exact search until semantic local search exists?
- Should `remote` be named `hosted` in the CLI, or is `remote` clearer for users?
