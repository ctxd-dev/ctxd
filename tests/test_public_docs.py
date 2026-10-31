import argparse
import ast
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from ctxd.cli import _build_parser
from ctxd.local_ctxfs_server import CtxfsAuthMode, create_ctxfs_app
from ctxd.public_environment import PublicEnvironmentVariable
from ctxfs.auth import Scope


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


def test_public_surface_contract_matches_http_routes() -> None:
    contract = _surface_contract()["routes"]["entries"]
    with tempfile.TemporaryDirectory() as directory:
        app = create_ctxfs_app(
            Path(directory), auth_mode=CtxfsAuthMode.TRUSTED_LOCAL
        )
        runtime = {
            f"{method} {route.path}"
            for route in app.routes
            if not route.path.startswith(("/openapi", "/docs", "/redoc"))
            for method in (route.methods or set()) - {"HEAD", "OPTIONS"}
        }

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
    assert "CTXD_BACKEND" not in text
    assert "CTXD_CTXFS_SOCKET" in text
    assert "CTXD_CTXFS_URL" in text
    assert "CTXD_LOCAL_HOME" not in text
    assert "CTXD_LOCAL_CTXFS_HOST" not in text
    assert "CTXD_LOCAL_CTXFS_PORT" not in text


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
