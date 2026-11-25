# Indexed-search rollout gates

Indexed search ships with the release-candidate configuration: ten results and
up to five 800-byte excerpts by default. Callers that only need ranking metadata
can explicitly pass `excerpt_bytes=0` and `excerpt_limit=0`.

## Pre-release gates

1. Run both complete repository test suites.
2. Run `ctxd server index rebuild --json` for an existing catalog; ordinary
   reads, grep and writes remain available while the index is not ready, and
   search returns `503`.
3. Build wheel and source distributions and smoke-test the wheel.
4. Run `scripts/benchmark_search_concurrency.py`; every reported check must
   pass.
5. Verify the CI SQLite/FTS5 probe on Python 3.11 and 3.12, Linux x86-64 and
   arm64, and macOS arm64. These are the platforms supported by the local
   tracker; Windows is not currently a supported local-tracker target.
6. Confirm migration, recovery, authorization, packaging, concurrency, and
   rollback tests.
7. Run the untouched 400-question held-out split once. Do not tune this release
   from its per-question output.

## Telemetry and alerts

Collect `ctxfs_search` and `ctxfs_search_index_rebuild` log events. Monitor
latency, failures, freshness, selected-term count, response bytes, excerpt
fallbacks, rebuild progress, and rollback/failure events. Never collect query
text, path names, excerpts, or document content.

Initial synthetic mixed-load guardrails are: cold search at most 1,000 ms,
warm p95 at most 500 ms, warm p99 at most 1,000 ms, process RSS at most 512
MiB, SQLite temporary files at most 64 MiB, response payload at most 64 KiB,
and at least five write operations per second. Re-establish these thresholds on
production-like corpus sizes and hardware before broad rollout.

Keep raw grep as the exact regular-expression complement to indexed search.
Because this contract has not shipped, do not retain migration or cursor paths
solely for pre-release implementations. Stop the release if any gate fails;
fixes require a new release candidate and a complete gate rerun.
