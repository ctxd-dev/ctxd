"""Trace real ctxd MCP source changes in a local Phoenix instance."""
import argparse
import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

# Prevent ambient Phoenix configuration from exporting synthetic traces elsewhere.
for key in tuple(os.environ):
    if key.startswith(("PHOENIX_", "OTEL_")):
        os.environ.pop(key)
os.environ["PHOENIX_DISCOVER_CONFIG"] = "false"

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from opentelemetry.sdk.resources import Resource
from phoenix.client import Client
from phoenix.otel import register

IMAGE = "ghcr.io/ctxddev/ctxd-mcp@sha256:44323c50f410893e4d2e7f778e9d1a4da36e4a44a46abeaed25de5c91ff0eb11"
PROJECT = "ctxd-source-lifecycle"
INITIAL = "Read the deployment guide before changing configuration.\n"
EDITED = "Read the deployment guide and check readiness before changing configuration.\n"


async def exercise(tracer):
    checkpoints = []
    with tempfile.TemporaryDirectory(prefix="ctxd-source-") as temporary:
        source = Path(temporary)
        source.chmod(0o755)
        guide = source / "guide.md"
        guide.write_text(INITIAL)
        guide.chmod(0o644)
        parameters = StdioServerParameters(
            command="docker",
            args=[
                "run", "--rm", "-i", "--init", "--network=none",
                "--cap-drop=ALL", "--security-opt=no-new-privileges",
                "--mount", f"type=bind,src={source},dst=/sources,readonly", IMAGE,
            ],
        )
        # Container startup diagnostics are not trace attributes or public output.
        with open(os.devnull, "w") as errors:
            async with stdio_client(parameters, errlog=errors) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()

                    async def call(tool, arguments):
                        with tracer.start_as_current_span(
                            f"ctxd.{tool}", openinference_span_kind="tool",
                            record_exception=False,
                        ) as span:
                            span.set_input(arguments)
                            span.set_attribute("tool.name", tool)
                            response = await session.call_tool(tool, arguments)
                            if response.is_error:
                                raise RuntimeError(f"MCP {tool} failed")
                            payload = json.loads(next(
                                item.text for item in response.content if item.type == "text"
                            ))
                            span.set_output(payload)
                            return payload

                    def passed(span, stage, observation):
                        span.set_output(observation)
                        checkpoints.append({
                            "span_id": f"{span.get_span_context().span_id:016x}",
                            "stage": stage,
                            "observation": observation,
                        })

                    with tracer.start_as_current_span(
                        "source.lifecycle", openinference_span_kind="chain",
                        record_exception=False,
                    ) as workflow:
                        trace_id = f"{workflow.get_span_context().trace_id:032x}"
                        workflow.set_input({"fixture": "synthetic deployment guide"})
                        with tracer.start_as_current_span(
                            "initial_read", openinference_span_kind="chain",
                            record_exception=False,
                        ) as checkpoint:
                            checkpoint.set_input({"expected_text": INITIAL})
                            found = await call("glob", {"pattern": "**/guide.md", "limit": 10})
                            path = found["items"][0]["path"]
                            result = await call("read", {"path": path, "max_bytes": 1000})
                            assert result["text"] == INITIAL
                            passed(checkpoint, "initial_read", result)

                        guide.write_text(EDITED)
                        with tracer.start_as_current_span(
                            "after_edit_sync", openinference_span_kind="chain",
                            record_exception=False,
                        ) as checkpoint:
                            checkpoint.set_input({"expected_text": EDITED})
                            deadline = time.monotonic() + 35
                            while True:
                                result = await call("read", {"path": path, "max_bytes": 1000})
                                if result["text"] == EDITED:
                                    break
                                if time.monotonic() >= deadline:
                                    raise AssertionError("Edited source was not synchronized")
                                await asyncio.sleep(1)
                            passed(checkpoint, "after_edit_sync", result)

                        guide.unlink()
                        with tracer.start_as_current_span(
                            "after_removal_sync", openinference_span_kind="chain",
                            record_exception=False,
                        ) as checkpoint:
                            checkpoint.set_input({"expected_matching_paths": []})
                            deadline = time.monotonic() + 35
                            while True:
                                result = await call("glob", {"pattern": "**/guide.md", "limit": 10})
                                if not result["items"]:
                                    break
                                if time.monotonic() >= deadline:
                                    raise AssertionError("Removed source remains discoverable")
                                await asyncio.sleep(1)
                            passed(checkpoint, "after_removal_sync", result)
                        workflow.set_output({"checkpoints_passed": len(checkpoints)})
    return trace_id, checkpoints


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:6006")
    parser.add_argument("--output", type=Path, default=Path("phoenix-source-check.json"))
    args = parser.parse_args()
    endpoint = urlsplit(args.endpoint)
    if endpoint.scheme != "http" or endpoint.hostname not in {"localhost", "127.0.0.1"}:
        parser.error("Use a local Phoenix HTTP endpoint")
    provider = register(
        endpoint=args.endpoint.rstrip("/") + "/v1/traces",
        project_name=PROJECT, protocol="http/protobuf",
        batch=False, auto_instrument=False, verbose=False, api_key="",
        set_global_tracer_provider=False,
        resource=Resource({"service.name": "ctxd-source-example"}),
    )
    try:
        trace_id, checkpoints = asyncio.run(asyncio.wait_for(exercise(provider.get_tracer("ctxd.example")), 130))
        assert provider.force_flush(timeout_millis=10000)
        client = Client(base_url=args.endpoint, api_key="")
        deadline = time.monotonic() + 15
        while True:
            spans = client.spans.get_spans(project_identifier=PROJECT, trace_ids=[trace_id])
            ids = {span["context"]["span_id"] for span in spans}
            if all(item["span_id"] in ids for item in checkpoints):
                break
            if time.monotonic() >= deadline:
                raise AssertionError("Phoenix did not retain the checkpoint spans")
            time.sleep(0.25)
        client.spans.log_span_annotations(span_annotations=[{
            "span_id": item["span_id"], "name": "source_lifecycle_check",
            "annotator_kind": "CODE",
            "result": {"label": "passed", "score": 1.0,
                       "explanation": f"Synthetic assertion passed: {item['stage']}"},
        } for item in checkpoints], sync=True)
        annotations = client.spans.get_span_annotations(
            project_identifier=PROJECT, span_ids=[item["span_id"] for item in checkpoints],
            include_annotation_names=["source_lifecycle_check"],
        )
        assert len(annotations) == 3
        result = {"image": IMAGE, "project": PROJECT, "trace_id": trace_id,
                  "checkpoints": checkpoints,
                  "spans": spans, "annotations": annotations, "passed": True}
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({"passed": True, "spans": len(spans), "annotations": len(annotations)}))
    finally:
        provider.shutdown()


if __name__ == "__main__":
    main()
