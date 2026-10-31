import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ctxd.config import CtxfsEndpointResolution
from ctxd.tracker_service import TrackerServicePaths, main, start, status, stop


class _Process:
    def __init__(self, pid: int = 789) -> None:
        self.pid = pid


def _paths(tmp_path: Path) -> TrackerServicePaths:
    return TrackerServicePaths(
        local_home=tmp_path / "local",
        pid_file=tmp_path / "local" / "tracker.pid",
    )


def test_tracker_service_start_writes_marked_pid(tmp_path: Path) -> None:
    paths = _paths(tmp_path)

    with patch(
        "ctxd.tracker_service.resolve_ctxfs_endpoint_details",
        return_value=CtxfsEndpointResolution(
            "unix:///tmp/ctxfs.sock", "default-managed-socket", True
        ),
    ), patch("ctxd.tracker_service.ensure_started") as ensure_started, patch(
        "ctxd.tracker_service.subprocess.Popen", return_value=_Process()
    ) as popen, patch(
        "ctxd.tracker_service._pid_matches", return_value=True
    ), patch(
        "ctxd.tracker_service.list_folders", return_value=[]
    ):
        result = start(paths)

    assert result["running"] is True
    assert result["pid"] == 789
    assert '"pid": 789' in paths.pid_file.read_text()
    assert '"marker": "ctxd.tracker_service"' in paths.pid_file.read_text()
    ensure_started.assert_called_once_with()
    popen.assert_called_once()


def test_tracker_service_start_checks_external_endpoint_without_local_start(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)

    with patch.dict(
        "os.environ", {"CTXD_CTXFS_URL": "http://ctxfs.internal:8765"}, clear=False
    ), patch("ctxd.tracker_service.ensure_started") as ensure_started, patch(
        "ctxd.tracker_service.CtxfsClient"
    ) as client, patch(
        "ctxd.tracker_service.subprocess.Popen", return_value=_Process()
    ), patch(
        "ctxd.tracker_service._pid_matches", return_value=True
    ), patch(
        "ctxd.tracker_service.list_folders", return_value=[]
    ):
        result = start(paths)

    assert result["running"] is True
    assert result["folders"] == 0
    ensure_started.assert_not_called()
    client.assert_called_once_with(endpoint="http://ctxfs.internal:8765", timeout=2.0)
    client.return_value.validate_authentication.assert_called_once_with()


def test_tracker_service_external_failure_does_not_start_tracker(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)

    with patch.dict(
        "os.environ", {"CTXD_CTXFS_URL": "http://ctxfs.internal:8765"}, clear=False
    ), patch("ctxd.tracker_service.ensure_started") as ensure_started, patch(
        "ctxd.tracker_service.CtxfsClient"
    ) as client, patch("ctxd.tracker_service.subprocess.Popen") as popen:
        client.return_value.validate_authentication.side_effect = RuntimeError(
            "endpoint unavailable"
        )

        try:
            start(paths)
        except RuntimeError as exc:
            assert str(exc) == "endpoint unavailable"
        else:
            raise AssertionError("start should fail when the endpoint is unavailable")

    ensure_started.assert_not_called()
    popen.assert_not_called()
    assert not paths.pid_file.exists()


def test_tracker_service_status_ignores_stale_or_unrelated_pid(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    paths.local_home.mkdir(parents=True)
    paths.pid_file.write_text('{"pid": 123, "marker": "ctxd.tracker_service"}\n')

    with patch("ctxd.tracker_service._pid_matches", return_value=False), patch(
        "ctxd.tracker_service.list_folders", return_value=[]
    ):
        result = status(paths)

    assert result["running"] is False
    assert result["pid"] == 123
    assert result["diagnostic_state"] == "not-attempted"


def test_tracker_service_status_reads_health_and_handles_corruption(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.local_home.mkdir(parents=True)
    health_path = paths.local_home / "tracker-health.json"
    health_path.write_text(
        '{"complete":false,"folders_scanned":2,"last_attempt":20,'
        '"last_error_category":null,"last_error_message":null,'
        '"last_result":"incomplete","last_success":10,"operations":1,"version":1}\n'
    )

    with patch("ctxd.tracker_service.list_folders", return_value=[]):
        result = status(paths)

    assert result["diagnostic_state"] == "available"
    assert result["last_result"] == "incomplete"
    assert result["last_success"] == 10
    assert result["folders_scanned"] == 2

    health_path.write_text("not json")
    with patch("ctxd.tracker_service.list_folders", return_value=[]):
        corrupt = status(paths)
    assert corrupt["diagnostic_state"] == "unavailable"
    assert "last_result" not in corrupt

    health_path.write_bytes(b"x" * 4097)
    with patch("ctxd.tracker_service.list_folders", return_value=[]):
        oversized = status(paths)
    assert oversized["diagnostic_state"] == "unavailable"


@pytest.mark.parametrize(
    "overrides",
    [
        {"version": True},
        {"version": "1"},
        {"last_result": "success", "last_success": 10, "complete": False},
        {
            "last_result": "failed",
            "last_success": 10,
            "complete": None,
            "folders_scanned": None,
            "operations": None,
        },
        {"last_success": 21},
    ],
)
def test_tracker_service_status_rejects_invalid_health_schema(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    paths = _paths(tmp_path)
    paths.local_home.mkdir(parents=True)
    health = {
        "version": 1,
        "last_attempt": 20,
        "last_success": None,
        "last_result": "incomplete",
        "last_error_category": None,
        "last_error_message": None,
        "complete": False,
        "folders_scanned": 2,
        "operations": 1,
    }
    health.update(overrides)
    (paths.local_home / "tracker-health.json").write_text(json.dumps(health))

    with patch("ctxd.tracker_service.list_folders", return_value=[]):
        result = status(paths)

    assert result["diagnostic_state"] == "unavailable"
    assert "last_result" not in result


def test_tracker_service_status_handles_unavailable_health_file(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    with patch("ctxd.tracker_service.Path.exists", side_effect=OSError("denied")), patch(
        "ctxd.tracker_service.load_tracker_health", return_value=None
    ), patch("ctxd.tracker_service.list_folders", return_value=[]):
        result = status(paths)

    assert result["diagnostic_state"] == "unavailable"
    assert result["running"] is False


def test_tracker_service_stop_does_not_signal_unrelated_pid(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.local_home.mkdir(parents=True)
    paths.pid_file.write_text('{"pid": 123, "marker": "ctxd.tracker_service"}\n')

    with patch("ctxd.tracker_service._pid_matches", return_value=False), patch(
        "ctxd.tracker_service.os.kill"
    ) as kill, patch("ctxd.tracker_service.list_folders", return_value=[]):
        result = stop(paths)

    assert result["running"] is False
    assert not paths.pid_file.exists()
    kill.assert_not_called()


def test_tracker_service_run_handles_keyboard_interrupt() -> None:
    with patch(
        "ctxd.tracker_service.run_forever", side_effect=KeyboardInterrupt
    ) as run_forever:
        exit_code = main(["run", "--interval", "0.5"])

    assert exit_code == 0
    call = run_forever.call_args
    assert call.kwargs["interval_seconds"] == 0.5
    assert call.kwargs["paths"].health_file.name == "tracker-health.json"
