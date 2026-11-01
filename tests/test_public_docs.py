import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from ctxd.cli import _build_parser
from ctxd.local_ctxfs_server import CtxfsAuthMode, create_ctxfs_app
from ctxd.public_environment import PublicEnvironmentVariable
from ctxfs.auth import Scope

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
SOURCE = Path(__file__).parents[1] / "src"


def _surface_contract() -> dict:
    return json.loads((PUBLIC_DOCS / "surface-contract.json").read_text())


def _assert_inventory(name: str, runtime: set[str], contract: set[str]) -> None:
    runtime_only = sorted(runtime - contract)
    contract_only = sorted(contract - runtime)
    assert not runtime_only and not contract_only, (
        f"{name} documentation drift in docs/public/surface-contract.json; "
        f"runtime only={runtime_only}; contract only={contract_only}"
    )


def _parser_inventory(parser: argparse.ArgumentParser) -> dict[str, dict[str, list[str]]]:
    inventory: dict[str, dict[str, list[str]]] = {}

    def walk(current: argparse.ArgumentParser, prefix: tuple[str, ...]) -> None:
        for action in current._actions:
            if not isinstance(action, argparse._SubParsersAction):
                continue
            for name, child in sorted(action.choices.items()):
                path = (*prefix, name)
                options = sorted(
                    option
                    for child_action in child._actions
                    for option in child_action.option_strings
                )
                positionals = sorted(
                    child_action.dest
                    for child_action in child._actions
                    if not child_action.option_strings
                    and not isinstance(child_action, argparse._SubParsersAction)
                )
                inventory[" ".join(path)] = {
                    "options": options,
                    "positionals": positionals,
                }
                walk(child, path)

    walk(parser, ())
    return inventory


def _assert_document_anchor(reference: str) -> None:
    filename, separator, anchor = reference.partition("#")
    assert separator and filename.endswith(".md"), reference
    text = (PUBLIC_DOCS / filename).read_text()
    headings = {
        heading.lower().replace("`", "").replace(" ", "-")
        for line in text.splitlines()
        if (heading := line.lstrip("#").strip()) and line.startswith("#")
    }
    assert anchor in headings, f"missing documentation anchor {reference}"


def _http_route_inventory(app) -> set[str]:
    return {
        f"{method} {route.path}"
        for route in app.routes
        for method in (route.methods or set()) - {"HEAD", "OPTIONS"}
    }


def _markdown_table_code_values(
    filename: str, heading: str, *, column: int
) -> set[str]:
    """Return backtick-delimited identifiers from one Markdown table column."""
    text = (PUBLIC_DOCS / filename).read_text()
    section_start = text.index(f"## {heading}")
    section = text[section_start:]
    next_heading = section.find("\n## ", 1)
    if next_heading != -1:
        section = section[:next_heading]
    values: set[str] = set()
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) <= column:
            continue
        match = re.fullmatch(r"`([^`]+)`", cells[column])
        if match:
            values.add(match.group(1))
    return values


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


def test_public_surface_contract_matches_cli_command_and_option_tree() -> None:
    contract = _surface_contract()["cli"]
    parser = _build_parser()
    runtime = _parser_inventory(parser)
    documented = contract["commands"]
    metadata = contract["commandMetadata"]

    _assert_inventory("CLI command", set(runtime), set(documented))
    _assert_inventory("CLI command classification", set(runtime), set(metadata))
    for command in sorted(runtime.keys() & documented.keys()):
        assert runtime[command] == documented[command], (
            f"CLI option/argument drift for {command!r} in "
            "docs/public/surface-contract.json; "
            f"runtime={runtime[command]}; contract={documented[command]}"
        )
    root_options = sorted(
        option for action in parser._actions for option in action.option_strings
    )
    assert root_options == contract["rootOptions"], (
        "CLI root option drift in docs/public/surface-contract.json; "
        f"runtime={root_options}; contract={contract['rootOptions']}"
    )
    allowed_classes = {"workflow", "optional-workflow", "administration"}
    for command, details in metadata.items():
        assert set(details) == {"classification", "document"}, command
        assert details["classification"] in allowed_classes, command
        _assert_document_anchor(details["document"])
    prose = _markdown_table_code_values(
        "cli.md", "Command coverage policy", column=0
    )
    _assert_inventory("CLI command prose", set(documented), prose)


def test_public_surface_contract_matches_http_routes() -> None:
    route_contract = _surface_contract()["routes"]
    contract = route_contract["entries"]
    excluded = route_contract["excludedFrameworkRoutes"]
    with tempfile.TemporaryDirectory() as directory:
        app = create_ctxfs_app(
            Path(directory), auth_mode=CtxfsAuthMode.TRUSTED_LOCAL
        )
        all_runtime = _http_route_inventory(app)

    assert all(reason.strip() for reason in excluded.values())
    _assert_inventory(
        "excluded framework route",
        all_runtime - set(contract),
        set(excluded),
    )
    runtime = all_runtime - set(excluded)

    _assert_inventory("HTTP route", runtime, set(contract))
    allowed_classes = {"public", "operational", "machine-lifecycle", "tracker-only"}
    invalid = {
        route: value
        for route, value in contract.items()
        if set(value) != {"classification", "purpose"}
        or value["classification"] not in allowed_classes
        or not value["purpose"].strip()
    }
    assert not invalid, f"invalid HTTP route classifications: {invalid}"
    prose = _markdown_table_code_values("api.md", "Operations", column=1)
    _assert_inventory("HTTP route prose", set(contract), prose)


def test_http_route_contract_does_not_hide_similarly_prefixed_public_routes() -> None:
    route_contract = _surface_contract()["routes"]
    with tempfile.TemporaryDirectory() as directory:
        app = create_ctxfs_app(
            Path(directory), auth_mode=CtxfsAuthMode.TRUSTED_LOCAL
        )

        @app.get("/docs-export")
        def docs_export() -> dict[str, bool]:
            return {"ok": True}

        runtime = _http_route_inventory(app) - set(
            route_contract["excludedFrameworkRoutes"]
        )

    with pytest.raises(AssertionError, match="GET /docs-export"):
        _assert_inventory("HTTP route", runtime, set(route_contract["entries"]))


def test_public_surface_contract_matches_authorization_scopes() -> None:
    runtime = {scope.value for scope in Scope}
    entries = _surface_contract()["scopes"]["entries"]
    documented = set(entries)
    _assert_inventory("authorization scope", runtime, documented)
    for scope, metadata in entries.items():
        assert set(metadata) == {"purpose", "leastPrivilege", "affectedEndpoints"}, scope
        assert metadata["purpose"].strip(), scope
        assert metadata["leastPrivilege"].strip(), scope
        assert isinstance(metadata["affectedEndpoints"], list), scope
        if scope == "status:write":
            assert metadata["affectedEndpoints"] == [], scope
            assert "no endpoint currently consumes it" in metadata["leastPrivilege"], scope
        else:
            assert metadata["affectedEndpoints"], scope
    prose = _markdown_table_code_values(
        "security.md", "Authorization scopes", column=0
    )
    _assert_inventory("authorization scope prose", documented, prose)


def test_public_surface_contract_matches_environment_registry() -> None:
    runtime = {variable.value for variable in PublicEnvironmentVariable}
    entries = _surface_contract()["environment"]["entries"]
    documented = set(entries)
    _assert_inventory("public environment variable", runtime, documented)
    for variable, metadata in entries.items():
        assert set(metadata) == {"purpose", "precedence"}, variable
        assert metadata["purpose"].strip(), variable
        assert metadata["precedence"].strip(), variable

    source_variables: set[str] = set()
    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                source_variables.update(re.findall(r"\bCTXD_[A-Z0-9_]+\b", node.value))
    _assert_inventory("CTXD_* source literal", source_variables, runtime)
    prose = _markdown_table_code_values("cli.md", "Environment", column=0)
    _assert_inventory("public environment variable prose", documented, prose)


def test_public_surface_contract_documentation_homes_exist() -> None:
    contract = _surface_contract()
    assert contract["schemaVersion"] == 1
    for section in ("cli", "routes", "scopes", "environment"):
        _assert_document_anchor(contract[section]["document"])


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


def test_public_docs_teach_bounded_scoped_grep_discovery() -> None:
    cli_text = (PUBLIC_DOCS / "cli.md").read_text()
    ctxfs_text = (PUBLIC_DOCS / "ctxfs.md").read_text()

    assert "ctxd files tree --depth 1" in cli_text
    assert "`ctxd files grep \"deployment\" --prefix slack`" in cli_text
    assert "ctxd files tree --depth 1" in ctxfs_text
    assert "MCP `tree` tool with `depth=1`" in ctxfs_text
    assert "for example `slack`" in ctxfs_text
    assert "`slack/`" not in cli_text
    assert "`slack/`" not in ctxfs_text


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
