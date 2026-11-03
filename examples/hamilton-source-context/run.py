"""A local Hamilton graph that keeps one synthetic source artifact in ctxd."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from importlib.metadata import version as _version

from hamilton import driver
from ctxd import CtxdError, CtxfsClient


def source_artifact(source_dir: str, desired_text: str, should_exist: bool) -> dict:
    """Apply the desired state before a downstream node synchronizes it."""
    path = Path(source_dir) / "instructions.md"
    changed = False
    if should_exist:
        if not path.exists() or path.read_text() != desired_text:
            path.write_text(desired_text)
            changed = True
    elif path.exists():
        path.unlink()
        changed = True
    return {"path": str(path), "text": desired_text, "exists": should_exist,
            "source_changed": changed}


def synchronized_artifact(source_artifact: dict) -> dict:
    """This dependency makes materialization finish before sync starts."""
    subprocess.run(
        [sys.executable, "-m", "ctxd.tracker_service", "sync-once"],
        check=True, capture_output=True, text=True, timeout=90,
    )
    return source_artifact


def context_read(synchronized_artifact: dict, known_path: str) -> dict:
    """Read through the real ctxfs client after sync; wait for applied changes."""
    client = CtxfsClient(timeout=5)
    expected = synchronized_artifact
    deadline = time.monotonic() + 20
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        matches = client.files.glob("**/instructions.md", limit=2).items
        if expected["exists"]:
            if len(matches) == 1:
                path = matches[0].path
                result = client.files.read(path)
                if result.text == expected["text"]:
                    return {"path": path, "exists": True, "text": result.text,
                            "content_hash": result.content_hash,
                            "source_changed": expected["source_changed"],
                            "read_attempts": attempts}
        else:
            if not known_path:
                raise ValueError("Removal verification needs a previously read path")
            try:
                client.files.read(known_path)
            except CtxdError as error:
                if error.status_code != 404:
                    raise
                if not matches:
                    return {"path": known_path, "exists": False, "status_code": 404,
                            "source_changed": expected["source_changed"],
                            "read_attempts": attempts}
        time.sleep(0.1)
    raise TimeoutError("ctxfs did not reach the requested source state within 20 seconds")


def _main() -> None:
    graph = driver.Builder().with_modules(sys.modules[__name__]).build()
    # No cache adapter: each execute must repeat the side-effect chain.
    first = "Read the project guide before changing the service.\n"
    edited = "Read the project guide and rollback steps before changing the service.\n"
    cases = [("initial", first, True), ("repeat_initial", first, True),
             ("edit", edited, True), ("repeat_edit", edited, True),
             ("remove", "", False), ("repeat_remove", "", False)]
    results = []
    known_path = ""
    with TemporaryDirectory(prefix="hamilton-ctxd-") as source_dir:
        for command in [["ctxd", "backend", "set", "ctxfs"],
                        ["ctxd", "folders", "add", source_dir, "--name", "Hamilton"]]:
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=90)
        for label, text, present in cases:
            result = graph.execute(["context_read"], inputs={
                "source_dir": source_dir, "desired_text": text,
                "should_exist": present, "known_path": known_path,
            })["context_read"]
            assert result["exists"] is present
            if present:
                assert result["text"] == text
                assert result["content_hash"] == "sha256:" + hashlib.sha256(text.encode()).hexdigest()
                if known_path:
                    assert result["path"] == known_path
                known_path = result["path"]
            else:
                assert result["status_code"] == 404
            assert result["source_changed"] is (not label.startswith("repeat_"))
            results.append({"case": label, **result})
    assert results[0]["content_hash"] == results[1]["content_hash"]
    assert results[2]["content_hash"] == results[3]["content_hash"]
    assert results[0]["content_hash"] != results[2]["content_hash"]
    print(json.dumps({"passed": True, "python": sys.version.split()[0],
                      "apache_hamilton": _version("apache-hamilton"), "ctxd": _version("ctxd"),
                      "cache_adapter": False, "background_tracker": False,
                      "runs": results}, indent=2))


if __name__ == "__main__":
    _main()
