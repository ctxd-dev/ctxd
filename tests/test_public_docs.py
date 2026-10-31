import json
from pathlib import Path
import subprocess
import sys

from ctxd.config import DEFAULT_CONFIG_PATH
from ctxd.local_ctxfs_service import default_paths as default_ctxfs_paths
from ctxd.secure_store import DEFAULT_CREDENTIALS_PATH
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
    assert "CTXD_CREDENTIALS_PATH" in text
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
            "Tracker synchronization metadata",
            f"`{_documented_home_path(default_tracker_paths().state_file)}`",
            "Persistent and sensitive",
        ),
        (
            "Tracker process ID",
            f"`{_documented_home_path(default_tracker_service_paths().pid_file)}`",
            "Runtime only",
        ),
        (
            "Managed ctxfs process ID, socket, and lock",
            f"`{_documented_home_path(ctxfs_paths.pid_file)}`, "
            f"`{_documented_home_path(ctxfs_paths.socket_path)}`, and "
            f"`{_documented_home_path(ctxfs_paths.lock_file)}`",
            "Runtime only",
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
    assert "Changing either variable does not move existing data" in text
    assert "Stopping the tracker" not in text
    assert "`ctxd tracker stop` stops the tracker" in text
    assert "Uninstalling the package also does not\ndelete `~/.ctxd`" in text
    assert "It does not\ndelete the original tracked source folders" in text
    assert "These permissions are not encryption" in text


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
    assert "Starting the tracker ensures the ctxfs server is running" not in text


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
