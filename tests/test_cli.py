from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from ctxd._metadata import SDK_VERSION
from ctxd.cli import main
from ctxd.config import CtxfsEndpointResolution
from ctxd.exceptions import CtxdAuthError
from ctxd.models import (
    CtxfsBounded,
    CtxfsDirectoryEntry,
    CtxfsEntry,
    CtxfsGrepMatch,
    CtxfsReadLinesResult,
    CtxfsReadResult,
)


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


def test_status_help_describes_endpoint_health_not_authentication() -> None:
    stdout = StringIO()
    with redirect_stdout(stdout), pytest.raises(SystemExit) as exc:
        main(["status", "--help"])
    assert exc.value.code == 0
    output = stdout.getvalue()
    assert "endpoint health" in output
    assert "default local ctxfs service" in output
    assert "authentication" not in output
    assert "API key" not in output


@pytest.mark.parametrize("command", ["search", "fetch", "profile", "backend"])
def test_removed_commands_are_unknown(command: str) -> None:
    with pytest.raises(SystemExit) as exc:
        main([command])
    assert exc.value.code == 2


def test_endpoint_set_validates_and_saves() -> None:
    stdout = StringIO()
    with patch("ctxd.cli.CtxfsClient") as client, patch(
        "ctxd.cli.save_ctxfs_endpoint"
    ) as save, redirect_stdout(stdout):
        assert main(["endpoint", "set", "https://ctxfs.example/"]) == 0
    client.return_value.validate_authentication.assert_called_once_with()
    save.assert_called_once_with("https://ctxfs.example")


def test_login_validates_prompted_key_before_saving() -> None:
    stdout = StringIO()
    with patch.dict("os.environ", {"CTXD_CTXFS_API_KEY": ""}, clear=False), patch(
        "ctxd.cli.resolve_ctxfs_api_key", return_value=None
    ), patch("ctxd.cli.sys.stdin.isatty", return_value=True), patch(
        "ctxd.cli.getpass.getpass", return_value="reader-token"
    ), patch("ctxd.cli.CtxfsClient") as client, patch(
        "ctxd.cli.save_ctxfs_api_key"
    ) as save, redirect_stdout(stdout):
        assert main(["login", "--endpoint", "https://ctxfs.example/"]) == 0

    client.assert_called_once_with(
        endpoint="https://ctxfs.example", api_key="reader-token", timeout=5.0
    )
    client.return_value.validate_authentication.assert_called_once_with()
    save.assert_called_once_with("reader-token", endpoint="https://ctxfs.example")
    assert "reader-token" not in stdout.getvalue()


def test_login_validation_failure_does_not_save() -> None:
    with patch.dict(
        "os.environ", {"CTXD_CTXFS_API_KEY": "bad-token"}, clear=False
    ), patch("ctxd.cli.CtxfsClient") as client, patch(
        "ctxd.cli.save_ctxfs_api_key"
    ) as save:
        client.return_value.validate_authentication.side_effect = CtxdAuthError(
            "unauthorized"
        )

        assert main(["login", "--endpoint", "https://ctxfs.example"]) == 1

    save.assert_not_called()


def test_status_checks_explicit_endpoint_with_endpoint_credentials() -> None:
    stdout = StringIO()
    with patch("ctxd.cli.CtxfsClient") as client, redirect_stdout(stdout):
        assert main(["status", "--endpoint", "https://ctxfs.example/"]) == 0

    client.assert_called_once_with(endpoint="https://ctxfs.example", timeout=5.0)
    client.return_value.status.assert_called_once_with()
    assert stdout.getvalue() == (
        "ctxfs endpoint: https://ctxfs.example\nctxfs healthy: true\n"
    )


def test_status_starts_default_local_ctxfs() -> None:
    stdout = StringIO()
    with patch(
        "ctxd.cli.resolve_ctxfs_endpoint_details",
        return_value=CtxfsEndpointResolution(
            "unix:///tmp/ctxfs.sock", "default-managed-socket", True
        ),
    ), patch("ctxd.cli.ensure_started") as start, patch(
        "ctxd.cli.CtxfsClient"
    ) as client, redirect_stdout(stdout):
        assert main(["status"]) == 0
    start.assert_called_once_with()
    client.return_value.status.assert_called_once_with()
    assert "ctxfs healthy: true" in stdout.getvalue()


def test_files_grep_uses_ctxfs_without_backend_selection() -> None:
    result = CtxfsBounded[CtxfsGrepMatch](items=[], complete=True)
    with patch("ctxd.cli.ensure_started"), patch("ctxd.cli.Client") as client:
        client.return_value.files.grep.return_value = result
        assert main(["files", "grep", "needle"]) == 0


def _named_folder_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"folders":{"Documents":{"name":"Documents",'
        '"path":"/tmp/Documents","prefix":"local-files/folder-1"}}}'
    )
    return config_path


@pytest.mark.parametrize(
    ("arguments", "method", "expected_args", "expected_kwargs"),
    [
        (
            ["files", "tree", "Documents", "--depth", "2", "--limit", "3"],
            "tree",
            ("local-files/folder-1",),
            {"depth": 2, "limit": 3},
        ),
        (
            ["files", "ls", "Documents/subdir", "--limit", "4"],
            "ls",
            ("local-files/folder-1/subdir",),
            {"limit": 4},
        ),
        (
            ["files", "glob", "*.md", "--prefix", "Documents", "--limit", "5"],
            "glob",
            ("local-files/folder-1/*.md",),
            {"prefix": "local-files/folder-1", "limit": 5},
        ),
        (
            ["files", "stat", "Documents/README.md"],
            "stat",
            ("local-files/folder-1/README.md",),
            {},
        ),
    ],
)
def test_file_metadata_commands_resolve_named_folders_and_emit_json(
    tmp_path: Path,
    arguments: list[str],
    method: str,
    expected_args: tuple[str, ...],
    expected_kwargs: dict[str, int | str],
) -> None:
    config_path = _named_folder_config(tmp_path)
    result = (
        CtxfsEntry(path="local-files/folder-1/README.md", kind="file")
        if method == "stat"
        else CtxfsBounded[CtxfsDirectoryEntry](
            items=[
                CtxfsDirectoryEntry(path="local-files/folder-1/README.md", kind="file")
            ],
            complete=True,
        )
    )
    operation = Mock(return_value=result)
    files = SimpleNamespace(**{method: operation})

    stdout = StringIO()
    with patch.dict(
        "os.environ", {"CTXD_CONFIG_PATH": str(config_path)}, clear=False
    ), patch("ctxd.cli.ensure_started"), patch(
        "ctxd.cli.Client", return_value=SimpleNamespace(files=files)
    ), redirect_stdout(stdout):
        assert main(arguments) == 0

    operation.assert_called_once_with(*expected_args, **expected_kwargs)
    assert '"path": "local-files/folder-1/README.md"' in stdout.getvalue()


def test_files_read_resolves_named_folder_and_warns_when_truncated(
    tmp_path: Path,
) -> None:
    config_path = _named_folder_config(tmp_path)
    read = Mock(
        return_value=CtxfsReadResult(
            path="local-files/folder-1/README.md",
            text="partial",
            complete=False,
            stopped_by="max_bytes",
        )
    )
    stdout = StringIO()
    stderr = StringIO()
    with patch.dict(
        "os.environ", {"CTXD_CONFIG_PATH": str(config_path)}, clear=False
    ), patch("ctxd.cli.ensure_started"), patch(
        "ctxd.cli.Client",
        return_value=SimpleNamespace(files=SimpleNamespace(read=read)),
    ), redirect_stdout(stdout), patch("sys.stderr", stderr):
        assert main(["files", "read", "Documents/README.md", "--max-bytes", "7"]) == 0

    read.assert_called_once_with("local-files/folder-1/README.md", max_bytes=7)
    assert stdout.getvalue() == "partial\n"
    assert stderr.getvalue() == "Warning: output truncated by max_bytes.\n"


def test_files_read_lines_resolves_named_folder_and_emits_requested_lines(
    tmp_path: Path,
) -> None:
    config_path = _named_folder_config(tmp_path)
    read_lines = Mock(
        return_value=CtxfsReadLinesResult(
            path="local-files/folder-1/README.md",
            start_line=2,
            end_line=3,
            lines=["two", "three"],
            complete=True,
        )
    )
    stdout = StringIO()
    with patch.dict(
        "os.environ", {"CTXD_CONFIG_PATH": str(config_path)}, clear=False
    ), patch("ctxd.cli.ensure_started"), patch(
        "ctxd.cli.Client",
        return_value=SimpleNamespace(files=SimpleNamespace(read_lines=read_lines)),
    ), redirect_stdout(stdout):
        assert (
            main(
                [
                    "files",
                    "read-lines",
                    "Documents/README.md",
                    "--start",
                    "2",
                    "--end",
                    "3",
                ]
            )
            == 0
        )

    read_lines.assert_called_once_with(
        "local-files/folder-1/README.md", start_line=2, end_line=3
    )
    assert stdout.getvalue() == "two\nthree\n"
