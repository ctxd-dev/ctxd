"""Mixed ctxfs indexed-search/write release gate using a synthetic corpus."""

from __future__ import annotations

import argparse
import json
import resource
import statistics
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ctxfs.models import DeleteOperation, DocClass, MoveOperation, Provenance, PutOperation
from ctxfs.store import CtxfsStore


def _put(path: str, key: str, version: int, text: str) -> PutOperation:
    return PutOperation(
        path=path,
        source_key=key,
        source_version=str(version),
        doc_class=DocClass.EDITABLE,
        file_format="md",
        text=text,
        provenance=Provenance(producer="concurrency-gate", producer_version="1"),
    )


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    position = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * percentile))))
    return ordered[position]


def run(root: Path, *, documents: int, searches: int, writes: int, workers: int) -> dict[str, float | int | bool]:
    writer = "benchmark-writer"
    user = "benchmark-user"
    store = CtxfsStore(root)
    store.register_prefix(user, writer, "bench")
    store.submit(
        user,
        writer,
        [
            _put(
                f"bench/document-{number:05d}.md",
                f"seed-{number}",
                1,
                f"# Document {number}\nconcurrent search retry policy qualifier-{number % 17}",
            )
            for number in range(documents)
        ],
    )
    store.close()

    def search_once(number: int) -> tuple[float, int]:
        current = CtxfsStore(root)
        started = time.perf_counter()
        try:
            page = current.search(
                user,
                f"retry policy qualifier-{number % 17}",
                limit=10,
                excerpt_bytes=800,
                excerpt_limit=5,
            )
            response_bytes = len(page.model_dump_json(exclude_none=True).encode())
        finally:
            current.close()
        return (time.perf_counter() - started) * 1000, response_bytes

    def write_once(number: int) -> float:
        current = CtxfsStore(root)
        started = time.perf_counter()
        key = f"live-{number}"
        first_path = f"bench/live-{number:05d}.md"
        moved_path = f"bench/moved-{number:05d}.md"
        try:
            current.register_prefix(user, writer, "bench")
            current.submit(user, writer, [_put(first_path, key, 1, "live retry policy")])
            entry = current.stat(user, first_path)
            assert entry is not None
            current.submit(
                user,
                writer,
                [MoveOperation(source_key=key, path=moved_path, source_version="2", expected_content_hash=entry.content_hash)],
            )
            current.submit(user, writer, [DeleteOperation(source_key=key, source_version="3")])
        finally:
            current.close()
        return (time.perf_counter() - started) * 1000

    cold_latency, cold_bytes = search_once(0)
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        search_results = list(pool.map(search_once, range(searches)))
        write_latencies = list(pool.map(write_once, range(writes)))
    elapsed = time.perf_counter() - started
    search_latencies = [value[0] for value in search_results]
    response_bytes = [value[1] for value in search_results]
    final = CtxfsStore(root)
    status = final.search_index_status()
    final.close()
    temporary_bytes = sum(
        path.stat().st_size
        for pattern in ("*-wal", "*-shm", "*-journal")
        for path in root.glob(pattern)
        if path.is_file()
    )
    return {
        "cold_ms": round(cold_latency, 3),
        "cold_response_bytes": cold_bytes,
        "warm_p50_ms": round(statistics.median(search_latencies), 3),
        "warm_p95_ms": round(_percentile(search_latencies, 0.95), 3),
        "warm_p99_ms": round(_percentile(search_latencies, 0.99), 3),
        "max_response_bytes": max(response_bytes),
        "max_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 3),
        "temporary_bytes": temporary_bytes,
        "write_p95_ms": round(_percentile(write_latencies, 0.95), 3),
        "write_operations_per_second": round((writes * 3) / elapsed, 3),
        "index_healthy": status.healthy,
        "index_fresh": status.fresh,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=int, default=200)
    parser.add_argument("--searches", type=int, default=100)
    parser.add_argument("--writes", type=int, default=20)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-cold-ms", type=float, default=1000)
    parser.add_argument("--max-p95-ms", type=float, default=500)
    parser.add_argument("--max-p99-ms", type=float, default=1000)
    parser.add_argument("--max-rss-mib", type=float, default=512)
    parser.add_argument("--max-temporary-bytes", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--max-response-bytes", type=int, default=64 * 1024)
    parser.add_argument("--min-write-operations-per-second", type=float, default=5)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ctxfs-concurrency-") as directory:
        result = run(
            Path(directory),
            documents=args.documents,
            searches=args.searches,
            writes=args.writes,
            workers=args.workers,
        )
    checks = {
        "cold_latency": result["cold_ms"] <= args.max_cold_ms,
        "warm_p95": result["warm_p95_ms"] <= args.max_p95_ms,
        "warm_p99": result["warm_p99_ms"] <= args.max_p99_ms,
        "memory": result["max_rss_mib"] <= args.max_rss_mib,
        "temporary_storage": result["temporary_bytes"] <= args.max_temporary_bytes,
        "response_bytes": result["max_response_bytes"] <= args.max_response_bytes,
        "write_throughput": result["write_operations_per_second"] >= args.min_write_operations_per_second,
        "index_health": result["index_healthy"] and result["index_fresh"],
    }
    print(json.dumps({"metrics": result, "checks": checks}, indent=2, sort_keys=True))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
