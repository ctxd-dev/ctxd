from pathlib import Path
from unittest.mock import patch

from ctxd.tracker_service import TrackerServicePaths, start, status, stop


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

    with patch("ctxd.tracker_service.ensure_started") as ensure_started, patch(
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
