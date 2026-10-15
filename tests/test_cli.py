from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest

from ctxd.cli import main
from ctxd.config import CtxfsEndpointResolution
from ctxd.exceptions import CtxdAuthError
from ctxd._metadata import SDK_VERSION
from ctxd.models import (
    CtxfsBounded,
    CtxfsDirectoryEntry,
    CtxfsGrepMatch,
    CtxfsReadLinesResult,
    CtxfsReadResult,
    DocumentResult,
    ProfileResult,
)


def test_cli_version_prints_sdk_version() -> None:
    stdout = StringIO()

    with redirect_stdout(stdout), pytest.raises(SystemExit) as exc:
        main(["--version"])

    assert exc.value.code == 0
    assert stdout.getvalue() == f"ctxd {SDK_VERSION}\n"


def test_cli_help_describes_commands() -> None:
    stdout = StringIO()

    with redirect_stdout(stdout), pytest.raises(SystemExit) as exc:
        main(["--help"])

    assert exc.value.code == 0
    output = stdout.getvalue()
    assert "Search and fetch content from your ctxd-connected apps" in output
    assert "login" in output
    assert "Store an API key for future CLI and SDK calls." in output
    assert "install-app" in output
    assert "Open the app installation page" in output
    assert "ctxd search text:deployment application:slack" in output


def test_cli_search_help_describes_query_and_json_output() -> None:
    stdout = StringIO()

    with redirect_stdout(stdout), pytest.raises(SystemExit) as exc:
        main(["search", "--help"])

    assert exc.value.code == 0
    output = stdout.getvalue()
    assert "Search indexed app content using ctxd DSL." in output
    assert "Search output is always JSON." in output
    assert "QUERY" in output
    assert "text:deployment application:slack" in output


def test_cli_profile_json_calls_sdk() -> None:
    stdout = StringIO()
    profile = ProfileResult(
        integration_access="# Integration Access\n- Slack (`slack`) [INSTALLED]",
        file_tree="",
    )

    with patch("ctxd.cli.Client.get_profile", return_value=profile), redirect_stdout(
        stdout
    ):
        exit_code = main(["profile", "--json"])

    assert exit_code == 0
    output = stdout.getvalue()
    assert '"integration_access"' in output
    assert '"file_tree"' in output


def test_cli_login_rejects_api_key_flag() -> None:
    stderr = StringIO()

    with patch("sys.stderr", stderr), pytest.raises(SystemExit) as exc:
        main(["login", "--api-key", "api-key-123"])

    assert exc.value.code == 2
    assert "unrecognized arguments: --api-key api-key-123" in stderr.getvalue()


def test_cli_ctxfs_login_validates_before_saving() -> None:
    stdout = StringIO()
    with patch.dict("os.environ", {"CTXD_CTXFS_API_KEY": ""}, clear=False), patch(
        "ctxd.cli.resolve_ctxfs_api_key", return_value=None
    ), patch("ctxd.cli.sys.stdin.isatty", return_value=True), patch(
        "ctxd.cli.getpass.getpass", return_value="reader-token"
    ), patch("ctxd.cli.CtxfsClient") as client, patch(
        "ctxd.cli.save_ctxfs_api_key"
    ) as save, redirect_stdout(stdout):
        exit_code = main(["login", "--endpoint", "https://ctxfs.example/"])

    assert exit_code == 0
    client.assert_called_once_with(
        endpoint="https://ctxfs.example", api_key="reader-token", timeout=5.0
    )
    client.return_value.validate_authentication.assert_called_once_with()
    save.assert_called_once_with("reader-token", endpoint="https://ctxfs.example")
    assert "reader-token" not in stdout.getvalue()


def test_cli_ctxfs_login_validation_failure_does_not_save() -> None:
    with patch.dict(
        "os.environ", {"CTXD_CTXFS_API_KEY": "bad-token"}, clear=False
    ), patch("ctxd.cli.CtxfsClient") as client, patch(
        "ctxd.cli.save_ctxfs_api_key"
    ) as save:
        client.return_value.validate_authentication.side_effect = CtxdAuthError(
            "unauthorized"
        )
        exit_code = main(["login", "--endpoint", "https://ctxfs.example"])

    assert exit_code == 1
    save.assert_not_called()


def test_cli_ctxfs_status_checks_authentication_without_requiring_status_scope() -> None:
    stdout = StringIO()
    with patch(
        "ctxd.cli.resolve_ctxfs_api_key", return_value="submit-only-token"
    ), patch("ctxd.cli.CtxfsClient") as client, redirect_stdout(stdout):
        exit_code = main(["status", "--endpoint", "https://ctxfs.example"])

    assert exit_code == 0
    client.assert_called_once_with(
        endpoint="https://ctxfs.example", api_key="submit-only-token", timeout=5.0
    )
    client.return_value.validate_authentication.assert_called_once_with()
    assert stdout.getvalue() == "Authenticated to ctxfs at https://ctxfs.example.\n"


def test_cli_login_saves_prompted_api_key() -> None:
    stdout = StringIO()
    profile = ProfileResult(
        integration_access="# Integration Access\n- Slack (`slack`) [INSTALLED]",
        file_tree="",
    )

    with patch.dict("os.environ", {"CTXD_API_KEY": ""}, clear=False), patch(
        "ctxd.cli.resolve_api_key", return_value=None
    ), patch("ctxd.cli.sys.stdin.isatty", return_value=True), patch(
        "ctxd.cli.getpass.getpass", return_value="prompted-api-key"
    ), patch("ctxd.cli.Client.get_profile", return_value=profile), patch(
        "ctxd.cli.save_api_key"
    ) as save_api_key, redirect_stdout(stdout):
        exit_code = main(["login"])

    assert exit_code == 0
    assert stdout.getvalue() == "Saved API key authentication.\n"
    save_api_key.assert_called_once_with("prompted-api-key")


def test_cli_login_stores_prompted_api_key_in_plaintext_credentials(
    tmp_path: Path,
) -> None:
    stdout = StringIO()
    profile = ProfileResult(
        integration_access="# Integration Access\n- Slack (`slack`) [INSTALLED]",
        file_tree="",
    )
    config_path = tmp_path / "config.json"
    credentials_path = tmp_path / "credentials.json"

    with patch.dict(
        "os.environ",
        {
            "CTXD_API_KEY": "",
            "CTXD_CONFIG_PATH": str(config_path),
        },
        clear=False,
    ), patch("ctxd.cli.resolve_api_key", return_value=None), patch(
        "ctxd.cli.sys.stdin.isatty", return_value=True
    ), patch("ctxd.cli.getpass.getpass", return_value="prompted-api-key"), patch(
        "ctxd.cli.Client.get_profile", return_value=profile
    ), redirect_stdout(stdout):
        exit_code = main(["login"])

    assert exit_code == 0
    assert stdout.getvalue() == "Saved API key authentication.\n"
    assert credentials_path.read_text() == '{\n  "api_key": "prompted-api-key"\n}\n'


def test_cli_login_validates_env_api_key_without_saving() -> None:
    stdout = StringIO()
    profile = ProfileResult(
        integration_access="# Integration Access\n- Slack (`slack`) [INSTALLED]",
        file_tree="",
    )

    with patch.dict("os.environ", {"CTXD_API_KEY": "env-api-key"}, clear=False), patch(
        "ctxd.cli.Client.get_profile", return_value=profile
    ), patch("ctxd.cli.save_api_key") as save_api_key, redirect_stdout(stdout):
        exit_code = main(["login"])

    assert exit_code == 0
    assert stdout.getvalue() == "API key authentication is valid.\n"
    save_api_key.assert_not_called()


def test_cli_login_prompts_for_missing_api_key() -> None:
    stdout = StringIO()
    profile = ProfileResult(
        integration_access="# Integration Access\n- Slack (`slack`) [INSTALLED]",
        file_tree="",
    )

    with patch.dict("os.environ", {"CTXD_API_KEY": ""}, clear=False), patch(
        "ctxd.cli.resolve_api_key", return_value=None
    ), patch("ctxd.cli.sys.stdin.isatty", return_value=True), patch(
        "ctxd.cli.getpass.getpass", return_value="prompted-api-key"
    ) as getpass, patch("ctxd.cli.Client.get_profile", return_value=profile), patch(
        "ctxd.cli.save_api_key"
    ) as save_api_key, redirect_stdout(stdout):
        exit_code = main(["login"])

    assert exit_code == 0
    assert stdout.getvalue() == "Saved API key authentication.\n"
    getpass.assert_called_once_with("ctxd API key: ")
    save_api_key.assert_called_once_with("prompted-api-key")


def test_cli_login_prompts_when_stored_key_lookup_fails() -> None:
    stdout = StringIO()
    profile = ProfileResult(
        integration_access="# Integration Access\n- Slack (`slack`) [INSTALLED]",
        file_tree="",
    )

    with patch.dict("os.environ", {"CTXD_API_KEY": ""}, clear=False), patch(
        "ctxd.cli.resolve_api_key",
        side_effect=CtxdAuthError("Unable to read stored ctxd credentials."),
    ), patch("ctxd.cli.sys.stdin.isatty", return_value=True), patch(
        "ctxd.cli.getpass.getpass", return_value="prompted-api-key"
    ) as getpass, patch("ctxd.cli.Client.get_profile", return_value=profile), patch(
        "ctxd.cli.save_api_key"
    ) as save_api_key, redirect_stdout(stdout):
        exit_code = main(["login"])

    assert exit_code == 0
    assert stdout.getvalue() == "Saved API key authentication.\n"
    getpass.assert_called_once_with("ctxd API key: ")
    save_api_key.assert_called_once_with("prompted-api-key")


def test_cli_login_uses_resolved_api_key() -> None:
    stdout = StringIO()
    profile = ProfileResult(
        integration_access="# Integration Access\n- Slack (`slack`) [INSTALLED]",
        file_tree="",
    )

    with patch.dict("os.environ", {"CTXD_API_KEY": ""}, clear=False), patch(
        "ctxd.cli.resolve_api_key", return_value="stored-api-key"
    ), patch("ctxd.cli.Client.get_profile", return_value=profile), patch(
        "ctxd.cli.save_api_key"
    ) as save_api_key, redirect_stdout(stdout):
        exit_code = main(["login"])

    assert exit_code == 0
    assert stdout.getvalue() == "API key authentication is valid.\n"
    save_api_key.assert_not_called()


def test_cli_login_validates_against_hosted_backend() -> None:
    stdout = StringIO()
    profile = ProfileResult(
        integration_access="# Integration Access\n- Slack (`slack`) [INSTALLED]",
        file_tree="",
    )

    with patch.dict(
        "os.environ",
        {"CTXD_API_KEY": "env-api-key", "CTXD_BACKEND": "ctxfs"},
        clear=False,
    ), patch("ctxd.cli.Client") as client_class, redirect_stdout(stdout):
        client_class.return_value.get_profile.return_value = profile
        exit_code = main(["login"])

    assert exit_code == 0
    client_class.assert_called_once_with(api_key="env-api-key", backend="hosted")
    assert stdout.getvalue() == "API key authentication is valid.\n"


def test_cli_login_requires_api_key() -> None:
    stderr = StringIO()

    with patch.dict("os.environ", {"CTXD_API_KEY": ""}, clear=False), patch(
        "ctxd.cli.resolve_api_key", return_value=None
    ), patch("ctxd.cli.sys.stdin.isatty", return_value=False), patch(
        "ctxd.cli.getpass.getpass"
    ) as getpass, patch("sys.stderr", stderr):
        exit_code = main(["login"])

    assert exit_code == 1
    assert (
        "Missing API key. Set `CTXD_API_KEY` or run `ctxd login` in an interactive terminal.\n"
        == stderr.getvalue()
    )
    getpass.assert_not_called()


def test_cli_status_reports_authenticated() -> None:
    stdout = StringIO()

    with patch(
        "ctxd.cli.resolve_api_key", return_value="stored-api-key"
    ), redirect_stdout(stdout):
        exit_code = main(["status"])

    assert exit_code == 0
    assert stdout.getvalue() == "Authenticated to ctxd.\n"


def test_cli_status_reports_unauthenticated() -> None:
    stderr = StringIO()

    with patch("ctxd.cli.resolve_api_key", return_value=None), patch(
        "sys.stderr", stderr
    ):
        exit_code = main(["status"])

    assert exit_code == 1
    assert (
        stderr.getvalue()
        == "Not authenticated: Missing API key. Set `CTXD_API_KEY` or run `ctxd login`.\n"
    )


def test_cli_logout_clears_api_key(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    credentials_path = tmp_path / "credentials.json"
    config_path.write_text('{\n  "base_url": "https://ctxd.example.com"\n}\n')
    credentials_path.write_text('{\n  "api_key": "token"\n}\n')
    stdout = StringIO()

    with patch.dict(
        "os.environ", {"CTXD_CONFIG_PATH": str(config_path)}, clear=False
    ), redirect_stdout(stdout):
        exit_code = main(["logout"])

    assert exit_code == 0
    assert stdout.getvalue() == "Cleared stored ctxd API key.\n"
    assert config_path.read_text() == '{\n  "base_url": "https://ctxd.example.com"\n}\n'
    assert not credentials_path.exists()


def test_cli_logout_preserves_backend_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    credentials_path = tmp_path / "credentials.json"
    config_path.write_text(
        '{\n  "backend": "ctxfs",\n  "base_url": "https://ctxd.example.com"\n}\n'
    )
    credentials_path.write_text('{\n  "api_key": "token"\n}\n')
    stdout = StringIO()

    with patch.dict(
        "os.environ", {"CTXD_CONFIG_PATH": str(config_path)}, clear=False
    ), redirect_stdout(stdout):
        exit_code = main(["logout"])

    assert exit_code == 0
    assert '"backend": "ctxfs"' in config_path.read_text()
    assert not credentials_path.exists()


def test_cli_install_app_prints_dashboard_url() -> None:
    stdout = StringIO()

    with patch("ctxd.cli.webbrowser.open", return_value=True), redirect_stdout(stdout):
        exit_code = main(["install-app"])

    assert exit_code == 0
    output = stdout.getvalue()
    assert "To install an app, go to:" in output
    assert "https://app.ctxd.dev/knowledge-base/add-application" in output


def test_cli_install_app_prints_dashboard_url_without_browser() -> None:
    stdout = StringIO()

    with redirect_stdout(stdout):
        exit_code = main(["install-app", "--no-browser"])

    assert exit_code == 0
    assert "https://app.ctxd.dev/knowledge-base/add-application" in stdout.getvalue()


def test_cli_fetch_returns_document() -> None:
    stdout = StringIO()
    document = DocumentResult(
        id="doc-1",
        app_name="slack",
        title="Deployment notes",
        url="slack://general/doc-1",
        text="Deploy completed successfully.",
    )

    with patch(
        "ctxd.cli.Client.fetch_document", return_value=document
    ), redirect_stdout(stdout):
        exit_code = main(["fetch", "doc-1"])

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Deployment notes" in output
    assert "slack://general/doc-1" in output
    assert "Deploy completed successfully." in output


def test_cli_search_returns_nonzero_exit_code_for_payload_errors() -> None:
    stdout = StringIO()

    with patch(
        "ctxd.cli.Client.search",
        return_value=type(
            "SearchResultLike",
            (),
            {"model_dump": lambda self: {"results": [], "error": "bad query"}},
        )(),
    ), redirect_stdout(stdout):
        exit_code = main(["search", "deployment"])

    assert exit_code == 1
    assert '"error": "bad query"' in stdout.getvalue()


def test_cli_search_prints_clean_message_for_network_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stderr = StringIO()
    monkeypatch.setenv("CTXD_API_KEY", "test-token")

    async def mock_post(self, url, *, headers, json):
        del self, url, headers, json
        raise httpx.ConnectError("[Errno 8] nodename nor servname provided")

    with patch("httpx.AsyncClient.post", mock_post), patch("sys.stderr", stderr):
        exit_code = main(["search", "text:deployment"])

    assert exit_code == 1
    assert stderr.getvalue() == (
        "Could not connect to ctxd at https://mcp.ctxd.dev/mcp. "
        "Check your internet connection and try again.\n"
    )


def test_cli_search_outputs_json_by_default() -> None:
    stdout = StringIO()

    with patch(
        "ctxd.cli.Client.search",
        return_value=type(
            "SearchResultLike",
            (),
            {
                "model_dump": lambda self: {
                    "results": [
                        {
                            "id": "doc-1",
                            "app_name": "slack",
                            "title": "Deployment notes",
                            "url": "slack://general/doc-1",
                            "text": "Deploy completed successfully.",
                            "metadata": {},
                        }
                    ]
                }
            },
        )(),
    ) as search, redirect_stdout(stdout):
        exit_code = main(["search", "deployment"])

    assert exit_code == 0
    search.assert_called_once_with("deployment")
    assert '"app_name": "slack"' in stdout.getvalue()


def test_cli_search_accepts_unquoted_query_tokens() -> None:
    stdout = StringIO()

    with patch(
        "ctxd.cli.Client.search",
        return_value=type(
            "SearchResultLike",
            (),
            {
                "model_dump": lambda self: {
                    "results": [],
                    "error": None,
                    "dsl_parse_error": None,
                }
            },
        )(),
    ) as search, redirect_stdout(stdout):
        exit_code = main(["search", "text:test", "application:slack"])

    assert exit_code == 0
    search.assert_called_once_with("text:test application:slack")
    assert '"results": []' in stdout.getvalue()


def test_cli_search_restores_shell_stripped_text_quotes() -> None:
    stdout = StringIO()

    with patch(
        "ctxd.cli.Client.search",
        return_value=type(
            "SearchResultLike",
            (),
            {
                "model_dump": lambda self: {
                    "results": [],
                    "error": None,
                    "dsl_parse_error": None,
                }
            },
        )(),
    ) as search, redirect_stdout(stdout):
        exit_code = main(["search", "text:deployment process", "application:slack"])

    assert exit_code == 0
    search.assert_called_once_with('text:"deployment process" application:slack')
    assert '"results": []' in stdout.getvalue()


def test_cli_search_outputs_json_for_empty_success() -> None:
    stdout = StringIO()

    with patch(
        "ctxd.cli.Client.search",
        return_value=type(
            "SearchResultLike",
            (),
            {
                "model_dump": lambda self: {
                    "results": [],
                    "error": None,
                    "dsl_parse_error": None,
                }
            },
        )(),
    ), redirect_stdout(stdout):
        exit_code = main(["search", "deployment"])

    assert exit_code == 0
    assert '"results": []' in stdout.getvalue()


def test_cli_config_set_backend_saves_backend(tmp_path: Path) -> None:
    stdout = StringIO()
    config_path = tmp_path / "config.json"

    with patch.dict(
        "os.environ",
        {"CTXD_CONFIG_PATH": str(config_path)},
        clear=False,
    ), redirect_stdout(stdout):
        exit_code = main(["config", "set", "backend", "ctxfs"])

    assert exit_code == 0
    assert stdout.getvalue() == "Backend set to ctxfs.\n"
    assert '"backend": "ctxfs"' in config_path.read_text()


def test_cli_backend_set_remote_saves_remote_backend(tmp_path: Path) -> None:
    stdout = StringIO()
    config_path = tmp_path / "config.json"

    with patch.dict(
        "os.environ",
        {"CTXD_CONFIG_PATH": str(config_path)},
        clear=False,
    ), redirect_stdout(stdout):
        exit_code = main(["backend", "set", "remote"])

    assert exit_code == 0
    assert stdout.getvalue() == "Backend set to remote.\n"
    assert '"backend": "remote"' in config_path.read_text()


def test_cli_backend_get_prints_remote_for_hosted_alias(tmp_path: Path) -> None:
    stdout = StringIO()
    config_path = tmp_path / "config.json"
    config_path.write_text('{\n  "backend": "hosted"\n}\n')

    with patch.dict(
        "os.environ",
        {"CTXD_CONFIG_PATH": str(config_path)},
        clear=False,
    ), redirect_stdout(stdout):
        exit_code = main(["backend", "get"])

    assert exit_code == 0
    assert stdout.getvalue() == "remote\n"


def test_cli_backend_status_uses_ctxfs_profile() -> None:
    stdout = StringIO()
    service = {
        "running": True,
        "healthy": True,
        "endpoint": "unix:///tmp/ctxfs.sock",
        "root": "/tmp/ctxfs",
        "error": None,
    }

    with patch.dict(
        "os.environ",
        {"CTXD_BACKEND": "ctxfs", "CTXD_CTXFS_SOCKET": "/tmp/override.sock"},
        clear=False,
    ), patch(
        "ctxd.cli.ctxfs_service_status", return_value=service
    ) as service_status, patch("ctxd.cli.CtxfsClient") as client, redirect_stdout(
        stdout
    ):
        exit_code = main(["backend", "status"])

    assert exit_code == 0
    service_status.assert_not_called()
    client.assert_called_once_with(endpoint="unix:///tmp/override.sock", timeout=2.0)
    client.return_value.status.assert_called_once_with()
    output = stdout.getvalue()
    assert "Backend: ctxfs" in output
    assert "ctxfs managed locally: False" in output
    assert "ctxfs endpoint source: environment-socket" in output
    assert "ctxfs running:" not in output
    assert "ctxfs healthy: True" in output
    assert "ctxfs endpoint: unix:///tmp/override.sock" in output


def test_cli_backend_status_reports_managed_local_process() -> None:
    stdout = StringIO()
    service = {
        "running": True,
        "healthy": True,
        "endpoint": "unix:///tmp/ctxfs.sock",
        "root": "/tmp/ctxfs",
        "error": None,
    }

    with patch.dict("os.environ", {"CTXD_BACKEND": "ctxfs"}, clear=False), patch(
        "ctxd.cli.resolve_ctxfs_endpoint_details",
        return_value=CtxfsEndpointResolution(
            "unix:///tmp/ctxfs.sock", "default-managed-socket", True
        ),
    ), patch(
        "ctxd.cli.ctxfs_service_status", return_value=service
    ) as service_status, patch("ctxd.cli.CtxfsClient") as client, redirect_stdout(
        stdout
    ):
        exit_code = main(["backend", "status"])

    assert exit_code == 0
    service_status.assert_called_once_with()
    client.assert_called_once_with(endpoint="unix:///tmp/ctxfs.sock", timeout=2.0)
    output = stdout.getvalue()
    assert "ctxfs managed locally: True" in output
    assert "ctxfs running: True" in output
    assert "ctxfs root: /tmp/ctxfs" in output


def test_cli_backend_set_ctxfs_starts_service(tmp_path: Path) -> None:
    stdout = StringIO()
    config_path = tmp_path / "config.json"
    service = {
        "running": True,
        "healthy": True,
        "endpoint": "unix:///tmp/ctxfs.sock",
        "root": "/tmp/ctxfs",
        "error": None,
    }

    with patch.dict(
        "os.environ",
        {"CTXD_CONFIG_PATH": str(config_path)},
        clear=False,
    ), patch(
        "ctxd.cli.resolve_ctxfs_endpoint_details",
        return_value=CtxfsEndpointResolution(
            "unix:///tmp/ctxfs.sock", "default-managed-socket", True
        ),
    ), patch(
        "ctxd.cli.ensure_started", return_value=service
    ) as ensure, redirect_stdout(stdout):
        exit_code = main(["backend", "set", "ctxfs"])

    assert exit_code == 0
    ensure.assert_called_once_with()
    output = stdout.getvalue()
    assert "Backend set to ctxfs." in output
    assert "ctxfs endpoint source: default-managed-socket" in output
    assert "ctxfs managed locally: True" in output
    assert "ctxfs endpoint: unix://" in output
    assert '"backend": "ctxfs"' in config_path.read_text()


def test_cli_backend_set_ctxfs_failure_does_not_save_backend(tmp_path: Path) -> None:
    stderr = StringIO()
    config_path = tmp_path / "config.json"

    with patch.dict(
        "os.environ",
        {"CTXD_CONFIG_PATH": str(config_path)},
        clear=False,
    ), patch(
        "ctxd.cli.resolve_ctxfs_endpoint_details",
        return_value=CtxfsEndpointResolution(
            "unix:///tmp/ctxfs.sock", "default-managed-socket", True
        ),
    ), patch(
        "ctxd.cli.ensure_started", side_effect=RuntimeError("service failed")
    ), patch("sys.stderr", stderr):
        exit_code = main(["backend", "set", "ctxfs"])

    assert exit_code == 1
    assert stderr.getvalue() == "service failed\n"
    assert not config_path.exists()


def test_cli_backend_set_ctxfs_checks_external_endpoint_without_local_start(
    tmp_path: Path,
) -> None:
    stdout = StringIO()
    config_path = tmp_path / "config.json"

    with patch.dict(
        "os.environ",
        {
            "CTXD_CONFIG_PATH": str(config_path),
            "CTXD_CTXFS_URL": "http://ctxfs.internal:8765",
        },
        clear=False,
    ), patch("ctxd.cli.ensure_started") as ensure, patch(
        "ctxd.cli.CtxfsClient"
    ) as client, redirect_stdout(stdout):
        exit_code = main(["backend", "set", "ctxfs"])

    assert exit_code == 0
    ensure.assert_not_called()
    client.assert_called_once_with(endpoint="http://ctxfs.internal:8765", timeout=2.0)
    client.return_value.validate_authentication.assert_called_once_with()
    output = stdout.getvalue()
    assert "ctxfs endpoint source: environment-url" in output
    assert "ctxfs managed locally: False" in output
    assert "ctxfs root:" not in output


def test_cli_backend_set_ctxfs_external_failure_does_not_save_backend(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"

    with patch.dict(
        "os.environ",
        {
            "CTXD_CONFIG_PATH": str(config_path),
            "CTXD_CTXFS_URL": "http://ctxfs.internal:8765",
        },
        clear=False,
    ), patch("ctxd.cli.ensure_started") as ensure, patch(
        "ctxd.cli.CtxfsClient"
    ) as client:
        client.return_value.validate_authentication.side_effect = RuntimeError(
            "endpoint unavailable"
        )
        exit_code = main(["backend", "set", "ctxfs"])

    assert exit_code == 1
    ensure.assert_not_called()
    assert not config_path.exists()


def test_cli_search_passes_ctxfs_options() -> None:
    stdout = StringIO()

    with patch(
        "ctxd.cli.Client.search",
        return_value=type(
            "SearchResultLike",
            (),
            {
                "model_dump": lambda self: {
                    "results": [],
                    "error": None,
                    "dsl_parse_error": None,
                }
            },
        )(),
    ) as search, redirect_stdout(stdout):
        exit_code = main(
            [
                "--backend",
                "ctxfs",
                "search",
                "needle",
                "--prefix",
                "local-files/root",
                "--limit",
                "3",
            ]
        )

    assert exit_code == 0
    search.assert_called_once_with("needle", prefix="local-files/root", limit=3)
    assert '"results": []' in stdout.getvalue()


def test_cli_search_accepts_command_local_backend_override() -> None:
    stdout = StringIO()

    with patch(
        "ctxd.cli.Client.search",
        return_value=type(
            "SearchResultLike",
            (),
            {
                "model_dump": lambda self: {
                    "results": [],
                    "error": None,
                    "dsl_parse_error": None,
                }
            },
        )(),
    ) as search, redirect_stdout(stdout):
        exit_code = main(
            [
                "search",
                "needle",
                "--backend",
                "ctxfs",
                "--prefix",
                "local-files/root",
            ]
        )

    assert exit_code == 0
    search.assert_called_once_with("needle", prefix="local-files/root")


def test_cli_files_tree_outputs_json() -> None:
    stdout = StringIO()
    tree = CtxfsBounded[CtxfsDirectoryEntry](
        items=[CtxfsDirectoryEntry(path="local-files/root/README.md", kind="file")],
        complete=True,
        stopped_by=None,
    )

    client = SimpleNamespace(files=SimpleNamespace(tree=lambda *args, **kwargs: tree))

    with patch("ctxd.cli.Client", return_value=client), redirect_stdout(stdout):
        exit_code = main(["files", "tree", "local-files/root", "--backend", "ctxfs"])

    assert exit_code == 0
    assert '"path": "local-files/root/README.md"' in stdout.getvalue()


def test_cli_files_grep_outputs_bounded_matches() -> None:
    stdout = StringIO()
    matches = CtxfsBounded[CtxfsGrepMatch](
        items=[
            CtxfsGrepMatch(
                path="local-files/root/README.md",
                line_number=2,
                line="needle here",
            )
        ],
        complete=True,
    )
    client = SimpleNamespace(
        files=SimpleNamespace(grep=lambda *args, **kwargs: matches)
    )

    with patch("ctxd.cli.Client", return_value=client), redirect_stdout(stdout):
        exit_code = main(
            [
                "files",
                "grep",
                "needle",
                "--prefix",
                "local-files/root",
                "--limit",
                "5",
                "--backend",
                "ctxfs",
            ]
        )

    assert exit_code == 0
    assert '"line": "needle here"' in stdout.getvalue()


def test_cli_files_read_lines_outputs_text() -> None:
    stdout = StringIO()
    lines = CtxfsReadLinesResult(
        path="local-files/root/README.md",
        start_line=1,
        end_line=2,
        lines=["one", "two"],
        complete=True,
        stopped_by=None,
    )

    client = SimpleNamespace(
        files=SimpleNamespace(read_lines=lambda *args, **kwargs: lines)
    )

    with patch("ctxd.cli.Client", return_value=client), redirect_stdout(stdout):
        exit_code = main(
            [
                "--backend",
                "ctxfs",
                "files",
                "read-lines",
                "local-files/root/README.md",
                "--start",
                "1",
                "--end",
                "2",
            ]
        )

    assert exit_code == 0
    assert stdout.getvalue() == "one\ntwo\n"


def test_cli_folders_add_list_and_remove_persist_config(tmp_path: Path) -> None:
    stdout = StringIO()
    config_path = tmp_path / "config.json"
    docs_path = tmp_path / "Docs"
    docs_path.mkdir()

    with patch.dict(
        "os.environ",
        {"CTXD_CONFIG_PATH": str(config_path)},
        clear=False,
    ), redirect_stdout(stdout):
        add_code = main(["folders", "add", str(docs_path), "--name", "Documents"])
        list_code = main(["folders", "list"])
        remove_code = main(["folders", "remove", "Documents"])

    assert add_code == 0
    assert list_code == 0
    assert remove_code == 0
    output = stdout.getvalue()
    assert f"Added folder Documents: {docs_path}" in output
    assert f"Documents\t{docs_path}" in output
    assert "Removed folder Documents." in output
    assert '"folders": {}' in config_path.read_text()


@pytest.mark.parametrize(
    ("folder_name", "message"),
    [
        ("local-files", "Folder name `local-files` is reserved."),
        ("Team/Docs", "Folder name cannot contain path separators."),
    ],
)
def test_cli_folders_add_rejects_invalid_names(
    tmp_path: Path, folder_name: str, message: str
) -> None:
    stderr = StringIO()
    config_path = tmp_path / "config.json"
    docs_path = tmp_path / "Docs"
    docs_path.mkdir()

    with patch.dict(
        "os.environ",
        {"CTXD_CONFIG_PATH": str(config_path)},
        clear=False,
    ), patch("sys.stderr", stderr):
        exit_code = main(["folders", "add", str(docs_path), "--name", folder_name])

    assert exit_code == 1
    assert message in stderr.getvalue()
    assert not config_path.exists()


def test_cli_folders_add_rejects_missing_path(tmp_path: Path) -> None:
    stderr = StringIO()
    config_path = tmp_path / "config.json"
    missing_path = tmp_path / "missing"

    with patch.dict(
        "os.environ",
        {"CTXD_CONFIG_PATH": str(config_path)},
        clear=False,
    ), patch("sys.stderr", stderr):
        exit_code = main(["folders", "add", str(missing_path), "--name", "Documents"])

    assert exit_code == 1
    assert f"Folder path `{missing_path}` does not exist." in stderr.getvalue()
    assert not config_path.exists()


def test_cli_folders_add_rejects_regular_file(tmp_path: Path) -> None:
    stderr = StringIO()
    config_path = tmp_path / "config.json"
    file_path = tmp_path / "notes.md"
    file_path.write_text("hello")

    with patch.dict(
        "os.environ",
        {"CTXD_CONFIG_PATH": str(config_path)},
        clear=False,
    ), patch("sys.stderr", stderr):
        exit_code = main(["folders", "add", str(file_path), "--name", "Documents"])

    assert exit_code == 1
    assert f"Folder path `{file_path}` is not a directory." in stderr.getvalue()
    assert not config_path.exists()


def test_cli_search_folder_maps_name_to_ctxfs_prefix(tmp_path: Path) -> None:
    stdout = StringIO()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        "{\n"
        '  "folders": {\n'
        '    "Documents": {\n'
        '      "name": "Documents",\n'
        '      "path": "/tmp/Documents",\n'
        '      "prefix": "local-files/folder-1"\n'
        "    }\n"
        "  }\n"
        "}\n"
    )

    with patch.dict(
        "os.environ",
        {"CTXD_CONFIG_PATH": str(config_path)},
        clear=False,
    ), patch(
        "ctxd.cli.Client.search",
        return_value=type(
            "SearchResultLike",
            (),
            {
                "model_dump": lambda self: {
                    "results": [],
                    "error": None,
                    "dsl_parse_error": None,
                }
            },
        )(),
    ) as search, redirect_stdout(stdout):
        exit_code = main(
            ["search", "needle", "--backend", "ctxfs", "--folder", "Documents"]
        )

    assert exit_code == 0
    search.assert_called_once_with("needle", prefix="local-files/folder-1")


def test_cli_search_rejects_folder_and_prefix_together(tmp_path: Path) -> None:
    stderr = StringIO()
    config_path = tmp_path / "config.json"

    with patch.dict(
        "os.environ",
        {"CTXD_CONFIG_PATH": str(config_path)},
        clear=False,
    ), patch("sys.stderr", stderr):
        exit_code = main(
            [
                "search",
                "needle",
                "--backend",
                "ctxfs",
                "--folder",
                "Documents",
                "--prefix",
                "local-files/root",
            ]
        )

    assert exit_code == 1
    assert "Use either `--prefix` or `--folder`, not both." in stderr.getvalue()


def test_cli_files_tree_resolves_folder_name(tmp_path: Path) -> None:
    stdout = StringIO()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        "{\n"
        '  "folders": {\n'
        '    "Documents": {\n'
        '      "name": "Documents",\n'
        '      "path": "/tmp/Documents",\n'
        '      "prefix": "local-files/folder-1"\n'
        "    }\n"
        "  }\n"
        "}\n"
    )
    tree = CtxfsBounded[CtxfsDirectoryEntry](
        items=[CtxfsDirectoryEntry(path="local-files/folder-1/README.md", kind="file")],
        complete=True,
        stopped_by=None,
    )

    client = SimpleNamespace(files=SimpleNamespace(tree=lambda *args, **kwargs: tree))

    with patch.dict(
        "os.environ",
        {"CTXD_CONFIG_PATH": str(config_path)},
        clear=False,
    ), patch("ctxd.cli.Client", return_value=client), redirect_stdout(stdout):
        exit_code = main(["files", "tree", "Documents", "--backend", "ctxfs"])

    assert exit_code == 0
    assert '"path": "local-files/folder-1/README.md"' in stdout.getvalue()


@pytest.mark.parametrize(
    ("arguments", "expected_pattern", "expected_prefix"),
    [
        (
            ["files", "glob", "Documents/*.md", "--backend", "ctxfs"],
            "local-files/folder-1/*.md",
            "",
        ),
        (
            [
                "files",
                "glob",
                "*.md",
                "--prefix",
                "Documents",
                "--backend",
                "ctxfs",
            ],
            "local-files/folder-1/*.md",
            "local-files/folder-1",
        ),
    ],
)
def test_cli_files_glob_resolves_folder_names(
    tmp_path: Path,
    arguments: list[str],
    expected_pattern: str,
    expected_prefix: str,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"folders":{"Documents":{"name":"Documents",'
        '"path":"/tmp/Documents","prefix":"local-files/folder-1"}}}'
    )
    result = CtxfsBounded[CtxfsDirectoryEntry](items=[], complete=True)
    glob = Mock(return_value=result)
    client = SimpleNamespace(files=SimpleNamespace(glob=glob))

    with patch.dict(
        "os.environ", {"CTXD_CONFIG_PATH": str(config_path)}, clear=False
    ), patch("ctxd.cli.Client", return_value=client):
        assert main(arguments) == 0

    glob.assert_called_once_with(
        expected_pattern,
        prefix=expected_prefix,
        limit=None,
    )


def test_cli_files_read_resolves_folder_name_and_outputs_text(tmp_path: Path) -> None:
    stdout = StringIO()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        "{\n"
        '  "folders": {\n'
        '    "Documents": {\n'
        '      "name": "Documents",\n'
        '      "path": "/tmp/Documents",\n'
        '      "prefix": "local-files/folder-1"\n'
        "    }\n"
        "  }\n"
        "}\n"
    )
    read_result = CtxfsReadResult(
        path="local-files/folder-1/README.md",
        text="hello\n",
        complete=True,
        stopped_by=None,
    )
    files = SimpleNamespace(read=lambda *args, **kwargs: read_result)
    client = SimpleNamespace(files=files)

    with patch.dict(
        "os.environ",
        {"CTXD_CONFIG_PATH": str(config_path)},
        clear=False,
    ), patch("ctxd.cli.Client", return_value=client), redirect_stdout(stdout):
        exit_code = main(["files", "read", "Documents/README.md", "--backend", "ctxfs"])

    assert exit_code == 0
    assert stdout.getvalue() == "hello\n"


def test_cli_tracker_start_outputs_status() -> None:
    stdout = StringIO()
    state = {"running": True, "pid": 123, "folders": 1}

    with patch("ctxd.cli.tracker_start", return_value=state) as start, redirect_stdout(
        stdout
    ):
        exit_code = main(["tracker", "start"])

    assert exit_code == 0
    start.assert_called_once_with()
    assert stdout.getvalue() == "Tracker started.\nFolders: 1\n"


def test_cli_tracker_status_outputs_state() -> None:
    stdout = StringIO()
    state = {"running": True, "pid": 123, "folders": 2}

    with patch("ctxd.cli.tracker_status", return_value=state), redirect_stdout(stdout):
        exit_code = main(["tracker", "status"])

    assert exit_code == 0
    assert stdout.getvalue() == "tracker running: True\ntracker pid: 123\nfolders: 2\n"


def test_cli_tracker_stop_outputs_status() -> None:
    stdout = StringIO()
    state = {"running": False, "pid": None, "folders": 1}

    with patch("ctxd.cli.tracker_stop", return_value=state) as stop, redirect_stdout(
        stdout
    ):
        exit_code = main(["tracker", "stop"])

    assert exit_code == 0
    stop.assert_called_once_with()
    assert stdout.getvalue() == "Tracker stopped.\n"


def test_cli_server_serve_defaults_to_managed_unix_socket() -> None:
    with patch("ctxd.cli.ctxfs_serve") as serve, patch(
        "ctxd.cli.default_ctxfs_paths"
    ) as paths:
        paths.return_value.root = Path("/tmp/ctxfs-root")
        paths.return_value.socket_path = Path("/tmp/ctxfs.sock")
        exit_code = main(["server", "serve"])

    assert exit_code == 0
    serve.assert_called_once_with(
        Path("/tmp/ctxfs-root"),
        socket_path=Path("/tmp/ctxfs.sock"),
        host="127.0.0.1",
        port=8765,
        allow_network=False,
        tls_cert=None,
        tls_key=None,
        credential_root=None,
        advertised_url=None,
    )


def test_cli_server_serve_passes_explicit_network_configuration(tmp_path: Path) -> None:
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    with patch("ctxd.cli.ctxfs_serve") as serve:
        exit_code = main(
            [
                "server",
                "serve",
                "--root",
                str(tmp_path / "data"),
                "--host",
                "::",
                "--port",
                "9443",
                "--allow-network",
                "--tls-cert",
                str(cert),
                "--tls-key",
                str(key),
            ]
        )
    assert exit_code == 0
    assert serve.call_args.kwargs["host"] == "::"
    assert serve.call_args.kwargs["allow_network"] is True
    assert serve.call_args.kwargs["tls_cert"] == cert
