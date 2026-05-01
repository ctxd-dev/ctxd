from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from ctxd.cli import main
from ctxd.exceptions import CtxdAuthError
from ctxd._metadata import SDK_VERSION
from ctxd.models import DocumentResult, ProfileResult


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
    ), patch(
        "ctxd.cli.Client.get_profile", return_value=profile
    ), patch(
        "ctxd.cli.save_api_key"
    ) as save_api_key, redirect_stdout(
        stdout
    ):
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
    ), patch(
        "ctxd.cli.getpass.getpass", return_value="prompted-api-key"
    ), patch(
        "ctxd.cli.Client.get_profile", return_value=profile
    ), redirect_stdout(
        stdout
    ):
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
    ) as getpass, patch(
        "ctxd.cli.Client.get_profile", return_value=profile
    ), patch(
        "ctxd.cli.save_api_key"
    ) as save_api_key, redirect_stdout(
        stdout
    ):
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
    ) as getpass, patch(
        "ctxd.cli.Client.get_profile", return_value=profile
    ), patch(
        "ctxd.cli.save_api_key"
    ) as save_api_key, redirect_stdout(
        stdout
    ):
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
    ) as save_api_key, redirect_stdout(
        stdout
    ):
        exit_code = main(["login"])

    assert exit_code == 0
    assert stdout.getvalue() == "API key authentication is valid.\n"
    save_api_key.assert_not_called()


def test_cli_login_requires_api_key() -> None:
    stderr = StringIO()

    with patch.dict("os.environ", {"CTXD_API_KEY": ""}, clear=False), patch(
        "ctxd.cli.resolve_api_key", return_value=None
    ), patch("ctxd.cli.sys.stdin.isatty", return_value=False), patch(
        "ctxd.cli.getpass.getpass"
    ) as getpass, patch(
        "sys.stderr", stderr
    ):
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
