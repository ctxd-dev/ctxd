# dlt loaded rows as a ctxd source

This example loads two synthetic service records into DuckDB with dlt, queries
the loaded table, and exports its rows as a UTF-8 Markdown snapshot. ctxd then
synchronizes that snapshot and reads it. A second load changes one record. A
final step retires the exported file and checks that ctxd returns path-not-found.

dlt performs extraction and loading. The example's SQL/export step materializes
the source content. ctxd handles synchronization and source reads. The DuckDB
database is outside the tracked source directory; ctxd does not read its binary
file. Retiring the exported snapshot leaves the loaded table intact.

## Run

From any Linux AMD64 workstation, as a user with Docker access, run these commands
in the directory containing this README:

```bash
docker build -t ctxd-dlt-example .
docker run --rm --network=none --cap-drop=ALL \
  --security-opt=no-new-privileges ctxd-dlt-example
```

The build downloads pinned dlt 1.32.0, DuckDB 1.5.6 and ctxd 0.1.16. dlt's DuckDB
extra requires DuckDB below 2. The run has no host data mounts or network access
and makes no model requests. It creates all data inside its disposable container.
dlt telemetry is disabled. Removing the container removes the fixture and stores.

## Evidence

The [Python example](source_snapshot.py) reads destination rows through dlt's SQL
client after each successful load, then atomically writes the snapshot before an
explicit ctxd synchronization. Every subsequent read is asserted against that
export. [Validation](validation.json) records the actual initial and changed
rows, snapshot hashes, returned content and path-not-found result. Both loads
completed without failed jobs. The retired export's two DuckDB rows remained.

This checks a local two-row workflow and a deliberate Markdown export. It does
not establish automatic database-change capture, a maintained dlt integration,
cloud-application connectors, retrieval quality or production scale. Production
exports need their own row selection, size bounds and access controls.

## Sources

- [DuckDB destination](https://dlthub.com/docs/dlt-ecosystem/destinations/duckdb)
- [Reading loaded rows with the SQL client](https://dlthub.com/docs/dlt-ecosystem/transformations/sql)
- [dlt telemetry controls](https://dlthub.com/docs/reference/telemetry)
