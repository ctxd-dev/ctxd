from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

import pytest

from ctxd._metadata import SDK_VERSION
from ctxd.cli import main
from ctxd.config import CtxfsEndpointResolution
from ctxd.models import CtxfsBounded, CtxfsGrepMatch


def test_cli_version_prints_sdk_version() -> None:
    stdout = StringIO()
    with redirect_stdout(stdout), pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert stdout.getvalue() == f"ctxd {SDK_VERSION}\n"


def test_cli_help_only_describes_ctxfs_commands() -> None:
    stdout = StringIO()
    with redirect_stdout(stdout), pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    output = stdout.getvalue()
    for command in ("files", "folders", "tracker", "endpoint", "server", "keys"):
        assert command in output
    for removed in ("search", "fetch", "profile", "backend", "install-app"):
        assert removed not in output


@pytest.mark.parametrize("command", ["search", "fetch", "profile", "backend"])
def test_removed_commands_are_unknown(command: str) -> None:
    with pytest.raises(SystemExit) as exc:
        main([command])
    assert exc.value.code == 2


def test_endpoint_set_validates_and_saves() -> None:
    stdout = StringIO()
    with patch("ctxd.cli.CtxfsClient") as client, patch("ctxd.cli.save_ctxfs_endpoint") as save, redirect_stdout(stdout):
        assert main(["endpoint", "set", "https://ctxfs.example/"]) == 0
    client.return_value.validate_authentication.assert_called_once_with()
    save.assert_called_once_with("https://ctxfs.example")


def test_status_starts_default_local_ctxfs() -> None:
    stdout = StringIO()
    with patch("ctxd.cli.resolve_ctxfs_endpoint_details", return_value=CtxfsEndpointResolution("unix:///tmp/ctxfs.sock", "default-managed-socket", True)), patch("ctxd.cli.ensure_started") as start, patch("ctxd.cli.CtxfsClient") as client, redirect_stdout(stdout):
        assert main(["status"]) == 0
    start.assert_called_once_with()
    client.return_value.status.assert_called_once_with()
    assert "ctxfs healthy: true" in stdout.getvalue()


def test_files_grep_uses_ctxfs_without_backend_selection() -> None:
    result = CtxfsBounded[CtxfsGrepMatch](items=[], complete=True)
    with patch("ctxd.cli.ensure_started"), patch("ctxd.cli.Client") as client:
        client.return_value.files.grep.return_value = result
        assert main(["files", "grep", "needle"]) == 0
