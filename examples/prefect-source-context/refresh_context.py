"""Run a local source update and verify it before the Prefect task succeeds."""

import hashlib
import json
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

from prefect import flow, task

SOURCE = Path('/tmp/ctxd-prefect-source/runbook.md')


@task(persist_result=False, retries=1, retry_delay_seconds=1)
def synchronize_and_read(content: str | None):
    # This task changes external state. Always execute the write and read check.
    if content is None:
        SOURCE.unlink(missing_ok=True)
    else:
        temporary = SOURCE.with_suffix('.tmp')
        temporary.write_text(content)
        temporary.replace(SOURCE)
    subprocess.run(
        [sys.executable, '-m', 'ctxd.tracker_service', 'sync-once'],
        check=True, capture_output=True, text=True, timeout=30,
    )
    read = subprocess.run(
        ['ctxd', 'files', 'read', 'PrefectExample/runbook.md'],
        capture_output=True, text=True, timeout=30,
    )
    if content is None:
        if read.returncode == 0 or read.stderr.strip() != 'ctxfs path was not found':
            raise RuntimeError('Removed source is readable, or its read failed unexpectedly')
        return {'state': 'removed'}
    if read.returncode or read.stdout != content:
        raise RuntimeError('The synchronized read does not match the requested snapshot')
    return {'state': 'readable', 'sha256': hashlib.sha256(content.encode()).hexdigest()}


@flow(persist_result=False)
def refresh_context(content: str | None):
    # A direct task call waits for its result. No remote worker is involved.
    return synchronize_and_read(content)


if __name__ == '__main__':
    SOURCE.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ctxd', 'backend', 'set', 'ctxfs'], check=True, capture_output=True, timeout=30)
    subprocess.run(['ctxd', 'folders', 'add', str(SOURCE.parent), '--name', 'PrefectExample'],
                   check=True, capture_output=True, timeout=30)
    snapshots = [
        'Run unit tests before release.\n',
        'Run unit tests before release.\n',
        'Run unit and integration tests before release.\n',
        None,
        None,
    ]
    results = []
    for index, snapshot in enumerate(snapshots):
        if index == 1:
            # The same parameters must still repair changed external source state.
            SOURCE.write_text('External edit that the desired snapshot replaces.\n')
        state = refresh_context(snapshot, return_state=True)
        if not state.is_completed():
            raise RuntimeError('The Prefect flow did not complete')
        result = state.result()
        if snapshot is not None and SOURCE.read_text() != snapshot:
            raise RuntimeError('The task did not materialize the requested source')
        if snapshot is None and SOURCE.exists():
            raise RuntimeError('The task did not remove the source')
        results.append({'operation': 'remove' if snapshot is None else 'upsert',
                        'flow_state': state.name, 'result': result})
    print('CTXD_PREFECT_RESULT=' + json.dumps({
        'prefect': version('prefect'), 'ctxd': version('ctxd'), 'runs': results,
        'passed': True, 'model_calls': 0, 'automatic_failure_retry_tested': False,
    }))
