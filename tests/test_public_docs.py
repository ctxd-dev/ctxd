import json
import os
import subprocess
import sys
from pathlib import Path

from ctxd.config import DEFAULT_CONFIG_PATH
from ctxd.local_ctxfs_service import default_paths as default_ctxfs_paths
from ctxd.secure_store import (
    CREDENTIALS_PATH_ENV_VAR,
    DEFAULT_CREDENTIALS_PATH,
    get_credentials_path,
)
from ctxd.tracker import default_tracker_paths
from ctxd.tracker_service import default_paths as default_tracker_service_paths

PUBLIC_DOCS = Path(__file__).parents[1] / "docs" / "public"


def _documented_home_path(path: Path, *, directory: bool = False) -> str:
    relative = path.relative_to(Path.home()).as_posix()
    suffix = "/" if directory else ""
    return f"~/{relative}{suffix}"


def test_public_docs_manifest_is_complete_and_safe() -> None:
    manifest = json.loads((PUBLIC_DOCS / "manifest.json").read_text())

    assert manifest["schemaVersion"] == 2
    documents = manifest["documents"]
    assert documents
    files = [document["file"] for document in documents]
    assert len(files) == len(set(files))
    assert all(Path(name).suffix == ".md" and ".." not in Path(name).parts for name in files)

    assert [document["order"] for document in documents] == sorted(
        document["order"] for document in documents
    )
    assert len({document["order"] for document in documents}) == len(documents)
    assert len({document["slug"] for document in documents}) == len(documents)
    assert all(
        set(document) == {"file", "slug", "title", "group", "order"}
        and document["file"] == f'{document["slug"]}.md'
        and document["title"].strip()
        and document["group"].strip()
        and isinstance(document["order"], int)
        for document in documents
    )

    actual = sorted(
        path.relative_to(PUBLIC_DOCS).as_posix()
        for path in PUBLIC_DOCS.rglob("*.md")
        if path.name != "README.md"
    )
    assert sorted(files) == actual


def test_public_docs_have_starlight_frontmatter() -> None:
    manifest = json.loads((PUBLIC_DOCS / "manifest.json").read_text())

    for document in manifest["documents"]:
        name = document["file"]
        text = (PUBLIC_DOCS / name).read_text()
        assert text.startswith("---\n"), name
        assert "\ntitle:" in text.split("---", 2)[1], name
        assert f"title: '{document['title']}'" in text.split("---", 2)[1], name


def test_cli_docs_only_list_supported_local_environment_variables() -> None:
    text = (PUBLIC_DOCS / "cli.md").read_text()

    assert "CTXD_CONFIG_PATH" in text
    assert f"`{CREDENTIALS_PATH_ENV_VAR}`" in text
    assert "CTXD_BACKEND" not in text
    assert "CTXD_CTXFS_SOCKET" in text
    assert "CTXD_CTXFS_URL" in text
    assert "CTXD_LOCAL_HOME" not in text
    assert "CTXD_LOCAL_CTXFS_HOST" not in text
    assert "CTXD_LOCAL_CTXFS_PORT" not in text


def test_security_docs_define_the_local_storage_contract() -> None:
    text = (PUBLIC_DOCS / "security.md").read_text()

    ctxfs_paths = default_ctxfs_paths()
    storage_table = text.split(
        "The managed local deployment uses these default paths:", 1
    )[1].split("The detached tracker", 1)[0]
    storage_rows = tuple(
        tuple(cell.strip() for cell in line.strip("|").split("|"))
        for line in storage_table.splitlines()
        if line.startswith("|")
    )
    assert storage_rows == (
        ("Data", "Default location", "Lifecycle"),
        ("---", "---", "---"),
        (
            "CLI settings, registered source paths, and generated ctxfs prefixes",
            f"`{_documented_home_path(DEFAULT_CONFIG_PATH)}`",
            "Persistent",
        ),
        (
            "Client bearer credentials and machine registration state",
            f"`{_documented_home_path(DEFAULT_CREDENTIALS_PATH)}`",
            "Persistent and sensitive",
        ),
        (
            "Credential write lock",
            f"`{_documented_home_path(DEFAULT_CREDENTIALS_PATH.with_suffix('.lock'))}`",
            "Persistent coordination file; contains no credential payload",
        ),
        (
            "Installation identity and machine registration locks",
            "`~/.ctxd/.ctxd-locks/identity-<digest>.lock` and "
            "`~/.ctxd/.ctxd-locks/register-<digest>.lock`",
            "Persistent coordination files; digest names vary and contain no "
            "credential payload",
        ),
        (
            "Tracker synchronization metadata",
            f"`{_documented_home_path(default_tracker_paths().state_file)}`",
            "Persistent and sensitive",
        ),
        (
            "Tracker current-health record",
            f"`{_documented_home_path(default_tracker_paths().health_file)}`",
            "Persistent",
        ),
        (
            "Tracker process ID",
            f"`{_documented_home_path(default_tracker_service_paths().pid_file)}`",
            "Runtime only",
        ),
        (
            "Managed ctxfs process ID and socket",
            f"`{_documented_home_path(ctxfs_paths.pid_file)}` "
            f"and `{_documented_home_path(ctxfs_paths.socket_path)}`",
            "Runtime only",
        ),
        (
            "Managed ctxfs coordination lock",
            f"`{_documented_home_path(ctxfs_paths.lock_file)}`",
            "Persistent coordination artifact; contains no service state",
        ),
        (
            "Managed ctxfs tree and catalog",
            f"`{_documented_home_path(ctxfs_paths.root, directory=True)}`",
            "Persistent and sensitive",
        ),
    )

    assert "The detached tracker does not currently create a log file" in text
    assert "`CTXD_CREDENTIALS_PATH` independently selects" in text
    assert "credentials file defaults to `credentials.json` beside" in text
    assert (
        "credential write lock and `.ctxd-locks/` directory are\ncreated beside "
        "the selected credentials file"
    ) in text
    assert (
        "Their lock files persist after use; ctxd does not remove\nthem automatically"
    ) in text
    assert (
        "managed ctxfs coordination lock also persists after the service exits; its\n"
        "runtime cleanup removes the process-ID file and socket, but not `ctxfs.lock`"
    ) in text
    assert "Changing either variable does not move existing data" in text
    assert "Stopping the tracker" not in text
    assert "`ctxd tracker stop` stops the tracker" in text
    assert "it does not stop the managed ctxfs service" in text
    assert "can remain running across\ntracker stop and package uninstall" in text
    assert "CLI currently has no managed ctxfs stop\ncommand" in text
    assert "identifies `ctxd.local_ctxfs_service`" in text
    assert "`ctxd tracker stop` is not\nsufficient for this step" in text
    assert "Uninstalling the package also does not\ndelete `~/.ctxd`" in text
    assert "It does not\ndelete the original tracked source folders" in text
    assert "These permissions are not encryption" in text


def test_documented_credentials_path_precedence_matches_runtime(
    monkeypatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "configuration" / "config.json"
    credentials_path = tmp_path / "secrets" / "client.json"

    monkeypatch.delenv(CREDENTIALS_PATH_ENV_VAR, raising=False)
    monkeypatch.setenv("CTXD_CONFIG_PATH", str(config_path))
    assert get_credentials_path() == config_path.parent / "credentials.json"

    monkeypatch.setenv(CREDENTIALS_PATH_ENV_VAR, str(credentials_path))
    assert get_credentials_path() == credentials_path

    replacement_config_path = tmp_path / "replacement" / "config.json"
    monkeypatch.setenv("CTXD_CONFIG_PATH", str(replacement_config_path))
    assert get_credentials_path() == credentials_path

    monkeypatch.delenv(CREDENTIALS_PATH_ENV_VAR)
    assert get_credentials_path() == replacement_config_path.parent / "credentials.json"

    text = (PUBLIC_DOCS / "cli.md").read_text()
    assert "credentials.json` beside the selected configuration file" in text
    assert "takes precedence" in text
    assert "does not migrate credentials or registration state" in text


def test_private_state_automation_example_is_self_contained(tmp_path: Path) -> None:
    text = (PUBLIC_DOCS / "cli.md").read_text()
    introduction = "For isolated automation or development, select both files explicitly:"
    example = text.split(introduction, 1)[1].split("```bash\n", 1)[1].split("```", 1)[0]

    executable = tmp_path / "bin" / "ctxd"
    executable.parent.mkdir()
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "from pathlib import Path\n"
        "private_state = Path(os.environ['CTXD_CONFIG_PATH']).parent\n"
        "assert private_state.is_dir()\n"
        "assert private_state.stat().st_mode & 0o777 == 0o700\n"
        "print(private_state)\n"
        "print(os.environ['CTXD_CONFIG_PATH'])\n"
        "print(os.environ['CTXD_CREDENTIALS_PATH'])\n"
    )
    executable.chmod(0o755)

    result = subprocess.run(
        ["bash", "-eu", "-c", example],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{executable.parent}:{os.environ['PATH']}"},
    )
    private_state, config_path, credentials_path = result.stdout.splitlines()

    assert config_path == f"{private_state}/config.json"
    assert credentials_path == f"{private_state}/credentials.json"
    assert not Path(private_state).exists()


def test_public_docs_do_not_reference_removed_cli_commands() -> None:
    removed_commands = (
        "ctxd config show",
        "ctxd daemon ",
        "ctxd service ",
        "ctxd folders repair-move",
        "ctxd version",
        "ctxd backend ",
        "ctxd search ",
        "ctxd fetch ",
    )

    for path in PUBLIC_DOCS.rglob("*.md"):
        text = path.read_text()
        for command in removed_commands:
            assert command not in text, f"{path.name}: {command}"


def test_public_docs_use_the_supported_local_workflow() -> None:
    public_text = "\n".join(
        path.read_text() for path in PUBLIC_DOCS.rglob("*.md")
    )

    assert "ctxd endpoint status" in public_text
    assert "ctxd files tree" in public_text
    assert "ctxd files grep" in public_text
    assert "POST /mcp" in public_text
    assert "ctxd-mcp" in public_text
    assert "~/.ctxd/local/ctxfs.sock" in public_text
    assert "curl " not in public_text
    assert "CTXD_BASE_URL" not in public_text
    assert "CTXD_API_KEY" not in public_text
    assert "http://127.0.0.1:8765" not in public_text
    assert "macOS, Linux, or Windows" not in public_text
    assert "ctxd folders remove <ROOT_ID>" not in public_text


def test_overview_describes_optional_tracker_and_endpoint_ownership() -> None:
    text = (PUBLIC_DOCS / "overview.md").read_text()

    assert "optional **local tracker**" in text
    assert "Read through the ctxd CLI" in text
    workflow = text.split("For example, to contribute and read a local folder:", 1)[1]
    workflow = workflow.split("```", 2)[1]
    add_folder = workflow.index("ctxd folders add ~/Documents --name Documents")
    start_tracker = workflow.index("ctxd tracker start")
    query_folder = workflow.index("ctxd files tree Documents")
    assert add_folder < start_tracker < query_folder
    assert "Starting the tracker ensures the ctxfs server is running" not in text


def test_tracker_docs_explain_incomplete_scan_stale_content_and_recovery() -> None:
    guide = (PUBLIC_DOCS / "local-tracker.md").read_text()
    quickstart = (PUBLIC_DOCS / "quickstart.md").read_text()

    assert "does not by itself guarantee that indexed content is current" in guide
    assert "does not infer deletions for the affected\nfolder" in guide
    assert "last successfully indexed version" in guide
    assert "replace invalid content with valid\nUTF-8" in guide
    assert "Restarting the tracker does\nnot correct it" in guide
    assert "`ctxd folders remove NAME` is different" in guide
    assert "does not guarantee current content when a scan is incomplete" in quickstart


def test_public_docs_describe_supported_authenticated_network_topology() -> None:
    public_text = "\n".join(path.read_text() for path in PUBLIC_DOCS.rglob("*.md"))

    required = (
        "ctxd server serve --root",
        "--allow-network --tls-cert",
        "ctxd keys --root /srv/ctxfs create",
        "--scope read",
        "--scope submit",
        "ctxd keys --root /srv/ctxfs rotate KEY_ID",
        "ctxd keys --root /srv/ctxfs revoke KEY_ID",
        "ctxd endpoint set https://",
        "ctxd login --endpoint https://",
        "ctxd status --endpoint https://",
        "ctxd logout --endpoint",
        "CTXD_CTXFS_API_KEY",
        "REST and MCP",
        "single ctxfs namespace",
    )
    for claim in required:
        assert claim in public_text

    unsafe_or_unsupported = (
        "Certificate verification is optional",
        "multi-tenant isolation is supported",
        "installs a background supervisor",
        "Loopback requests do not require",
        "shown by `ctxd folders list`",
    )
    for claim in unsafe_or_unsupported:
        assert claim not in public_text
    assert "Do not disable\ncertificate verification" in public_text
    assert "`ctxd folders list` does not\nprint generated prefixes" in public_text
    assert "Other clients use canonical paths" in public_text
    assert "MCP reader therefore normally\nneeds both `read` and `status`" in public_text


def test_security_docs_describe_os_account_isolation_without_claiming_a_sandbox() -> None:
    text = (PUBLIC_DOCS / "security.md").read_text()

    assert "runs under the invoking operating-system account" in text
    assert "user-only\npermissions" in text
    assert "is not\na separate security sandbox" in text
    assert "installed per user" not in text


def test_tracker_docs_match_structured_diagnostics_and_navigation() -> None:
    quickstart = (PUBLIC_DOCS / "quickstart.md").read_text()
    tracker = (PUBLIC_DOCS / "local-tracker.md").read_text()
    security = (PUBLIC_DOCS / "security.md").read_text()

    assert "for monitoring, moves, and removal" in quickstart
    assert "for upgrades, moves, logs, and removal" not in quickstart
    assert "last sync result: success" in tracker
    assert "last sync result: failed" in tracker
    assert "last sync result: incomplete" in tracker
    assert "does not currently create a log file" in security
    assert "configuration, state, and logs" not in security


def test_documented_network_command_groups_exist_in_cli_help() -> None:
    commands = (
        ("server", "serve"),
        ("keys", "create"),
        ("keys", "list"),
        ("keys", "rotate"),
        ("keys", "revoke"),
        ("endpoint", "set"),
        ("endpoint", "status"),
        ("login",),
        ("status",),
        ("logout",),
    )
    for command in commands:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from ctxd.cli import main; raise SystemExit(main())",
                *command,
                "--help",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (command, result.stderr)
        assert f"ctxd {' '.join(command)}" in result.stdout
