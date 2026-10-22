"""Public command-line interface for ctxd."""

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from ctxd import Client, CtxdError
from ctxd._metadata import SDK_VERSION
from ctxd.config import (
    clear_ctxfs_api_key,
    resolve_ctxfs_api_key,
    resolve_ctxfs_endpoint_details,
    save_ctxfs_api_key,
    save_ctxfs_endpoint,
)
from ctxd.folders import (
    add_folder,
    list_folders,
    remove_folder,
    try_resolve_named_path,
)
from ctxd.ctxfs_client import CtxfsClient
from ctxd.local_ctxfs_service import ensure_started, serve as ctxfs_serve
from ctxd.local_ctxfs_service import default_paths as default_ctxfs_paths
from ctxd.machine_identity import (
    configure_machine,
    machine_status,
    register_machine,
    unchanged_description,
)
from ctxd.tracker_service import (
    start as tracker_start,
    status as tracker_status,
    stop as tracker_stop,
)
from ctxfs.auth import Scope
from ctxfs.credentials import CredentialStore, IssuedCredential


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "login":
            return _handle_login(args)
        if args.command == "logout":
            return _handle_logout(args)
        if args.command == "status":
            return _handle_status(args)
        if args.command == "endpoint":
            return _handle_endpoint(args)
        if args.command == "machine":
            return _handle_machine(args)
        if args.command == "folders":
            return _handle_folders(args)
        if args.command == "tracker":
            return _handle_tracker(args)
        if args.command == "keys":
            return _handle_keys(args)
        if args.command == "server":
            return _handle_server(args)

        if args.command == "files":
            resolution = resolve_ctxfs_endpoint_details()
            if resolution.managed_local:
                ensure_started()
            client = Client(ctxfs_endpoint=resolution.endpoint)
            return _handle_files(args, client)
    except (CtxdError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    parser.error(f"Unknown command: {args.command}")
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ctxd",
        description=("Track local folders and inspect indexed content through ctxfs."),
        epilog=(
            "Examples:\n"
            "  ctxd folders add ~/Documents --name docs\n"
            "  ctxd tracker start\n"
            "  ctxd files tree\n"
            "  ctxd files read docs/README.md"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"ctxd {SDK_VERSION}",
        help="Print the installed ctxd version and exit.",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        metavar="<command>",
        title="commands",
        required=True,
    )

    login_parser = subparsers.add_parser(
        "login",
        help="Store an API key for future CLI and SDK calls.",
        description="Prompt for a ctxd API key and store it for future CLI and SDK calls.",
    )
    login_parser.add_argument(
        "--endpoint", help="Authenticate to this ctxfs HTTP endpoint."
    )
    logout_parser = subparsers.add_parser(
        "logout",
        help="Remove the stored API key from this machine.",
        description="Clear the locally stored ctxd API key.",
    )
    logout_parser.add_argument(
        "--endpoint", help="Remove credentials for this ctxfs HTTP endpoint."
    )
    status_parser = subparsers.add_parser(
        "status",
        help="Check ctxfs endpoint health.",
        description=(
            "Check ctxfs endpoint health, starting the default local ctxfs service "
            "when needed."
        ),
    )
    status_parser.add_argument(
        "--endpoint", help="Check this ctxfs HTTP(S) URL or Unix socket."
    )

    endpoint_parser = subparsers.add_parser(
        "endpoint", help="Configure or inspect the ctxfs endpoint."
    )
    endpoint_subparsers = endpoint_parser.add_subparsers(
        dest="endpoint_command", metavar="<endpoint-command>", required=True
    )
    endpoint_set = endpoint_subparsers.add_parser("set", help="Set the ctxfs endpoint.")
    endpoint_set.add_argument("url", help="HTTP(S) URL or unix:// socket.")
    endpoint_subparsers.add_parser("status", help="Show ctxfs endpoint health.")

    machine_parser = subparsers.add_parser(
        "machine", help="Configure and register this machine."
    )
    machine_subparsers = machine_parser.add_subparsers(
        dest="machine_command", metavar="<machine-command>", required=True
    )
    machine_register = machine_subparsers.add_parser(
        "register", help="Register this machine with ctxfs."
    )
    machine_register.add_argument("--endpoint")
    machine_register.add_argument("--name")
    _add_description_arguments(machine_register)
    machine_register.add_argument("--json", action="store_true")
    machine_configure = machine_subparsers.add_parser(
        "configure", help="Edit this machine's local metadata."
    )
    machine_configure.add_argument("--name")
    _add_description_arguments(machine_configure)
    machine_configure.add_argument("--json", action="store_true")
    machine_status_parser = machine_subparsers.add_parser(
        "status", help="Show local machine registration state."
    )
    machine_status_parser.add_argument("--endpoint")
    machine_status_parser.add_argument("--json", action="store_true")

    files_parser = subparsers.add_parser(
        "files",
        help="Read path-oriented content from ctxfs.",
        description="Read path-oriented content from ctxfs.",
    )
    files_subparsers = files_parser.add_subparsers(
        dest="files_command",
        metavar="<files-command>",
        required=True,
    )

    files_tree_parser = files_subparsers.add_parser("tree", help="List a bounded tree.")
    files_tree_parser.add_argument("prefix", nargs="?", default="")
    files_tree_parser.add_argument("--depth", type=int)
    files_tree_parser.add_argument("--limit", type=int)

    files_ls_parser = files_subparsers.add_parser("ls", help="List a directory.")
    files_ls_parser.add_argument("path", nargs="?", default="")
    files_ls_parser.add_argument("--limit", type=int)

    files_glob_parser = files_subparsers.add_parser("glob", help="Match paths by glob.")
    files_glob_parser.add_argument("pattern")
    files_glob_parser.add_argument("--prefix", default="")
    files_glob_parser.add_argument("--limit", type=int)

    files_grep_parser = files_subparsers.add_parser(
        "grep", help="Search file contents."
    )
    files_grep_parser.add_argument("pattern")
    files_grep_parser.add_argument("--prefix", default="")
    files_grep_parser.add_argument("--limit", type=int)

    files_stat_parser = files_subparsers.add_parser("stat", help="Show path metadata.")
    files_stat_parser.add_argument("path")

    files_read_lines_parser = files_subparsers.add_parser(
        "read-lines",
        help="Read a line range.",
    )
    files_read_lines_parser.add_argument("path")
    files_read_lines_parser.add_argument("--start", type=int, required=True)
    files_read_lines_parser.add_argument("--end", type=int, required=True)
    files_read_lines_parser.add_argument("--json", action="store_true")

    files_read_parser = files_subparsers.add_parser("read", help="Read a file.")
    files_read_parser.add_argument("path")
    files_read_parser.add_argument("--max-bytes", type=int)
    files_read_parser.add_argument("--json", action="store_true")

    folders_parser = subparsers.add_parser(
        "folders",
        help="Manage local folders tracked through ctxfs.",
        description="Manage local folders tracked through ctxfs.",
    )
    folders_subparsers = folders_parser.add_subparsers(
        dest="folders_command",
        metavar="<folders-command>",
        required=True,
    )
    folders_add_parser = folders_subparsers.add_parser(
        "add", help="Register a local folder."
    )
    folders_add_parser.add_argument("path")
    folders_add_parser.add_argument("--name")
    folders_subparsers.add_parser("list", help="List registered folders.")
    folders_remove_parser = folders_subparsers.add_parser(
        "remove", help="Remove a registered folder."
    )
    folders_remove_parser.add_argument("name")

    tracker_parser = subparsers.add_parser(
        "tracker",
        help="Control local folder tracking.",
        description="Control local folder tracking.",
    )
    tracker_subparsers = tracker_parser.add_subparsers(
        dest="tracker_command",
        metavar="<tracker-command>",
        required=True,
    )
    tracker_subparsers.add_parser("start", help="Start local folder tracking.")
    tracker_subparsers.add_parser("stop", help="Stop local folder tracking.")
    tracker_subparsers.add_parser("status", help="Show local tracker status.")

    keys_parser = subparsers.add_parser(
        "keys",
        help="Administer credentials in a local ctxfs server data root.",
        description="Administer credentials in a local ctxfs server data root.",
    )
    keys_parser.add_argument(
        "--root",
        type=Path,
        default=default_ctxfs_paths().root,
        help="ctxfs server data root (defaults to the managed local server root).",
    )
    keys_subparsers = keys_parser.add_subparsers(
        dest="keys_command", metavar="<keys-command>", required=True
    )
    keys_create = keys_subparsers.add_parser("create", help="Create a credential.")
    keys_create.add_argument("--name", required=True)
    keys_create.add_argument(
        "--scope",
        action="append",
        required=True,
        choices=tuple(scope.value for scope in Scope),
    )
    keys_create.add_argument("--prefix", action="append", default=[])
    keys_create.add_argument("--expires-at", type=float)
    keys_create.add_argument("--json", action="store_true")
    keys_list = keys_subparsers.add_parser("list", help="List credential metadata.")
    keys_list.add_argument("--json", action="store_true")
    keys_revoke = keys_subparsers.add_parser("revoke", help="Revoke a credential.")
    keys_revoke.add_argument("key_id")
    keys_revoke.add_argument("--json", action="store_true")
    keys_rotate = keys_subparsers.add_parser("rotate", help="Rotate a credential.")
    keys_rotate.add_argument("key_id")
    keys_rotate.add_argument("--json", action="store_true")

    server_parser = subparsers.add_parser(
        "server",
        help="Run a standalone ctxfs server.",
        description="Run a standalone ctxfs server with safe transport defaults.",
    )
    server_subparsers = server_parser.add_subparsers(
        dest="server_command", metavar="<server-command>", required=True
    )
    server_serve = server_subparsers.add_parser(
        "serve", help="Serve ctxfs in the foreground."
    )
    server_serve.add_argument("--root", type=Path, default=default_ctxfs_paths().root)
    listener = server_serve.add_mutually_exclusive_group()
    listener.add_argument("--socket", type=Path)
    listener.add_argument("--host")
    server_serve.add_argument("--port", type=int, default=8765)
    server_serve.add_argument("--allow-network", action="store_true")
    server_serve.add_argument("--tls-cert", type=Path)
    server_serve.add_argument("--tls-key", type=Path)
    server_serve.add_argument("--credential-root", type=Path)
    server_serve.add_argument("--advertised-url")

    return parser


def _add_description_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--description")
    group.add_argument("--clear-description", action="store_true")


def _handle_server(args: argparse.Namespace) -> int:
    if args.server_command != "serve":
        raise ValueError(f"Unknown server command: {args.server_command}")
    socket_path = args.socket
    if socket_path is None and args.host is None:
        socket_path = default_ctxfs_paths().socket_path
    ctxfs_serve(
        args.root,
        socket_path=socket_path,
        host=args.host or "127.0.0.1",
        port=args.port,
        allow_network=args.allow_network,
        tls_cert=args.tls_cert,
        tls_key=args.tls_key,
        credential_root=args.credential_root,
        advertised_url=args.advertised_url,
    )
    return 0


def _handle_keys(args: argparse.Namespace) -> int:
    with CredentialStore(args.root) as store:
        if args.keys_command == "create":
            issued = store.create(
                name=args.name,
                scopes=args.scope,
                prefixes=args.prefix,
                expires_at=args.expires_at,
            )
            return _emit_issued_credential(issued, as_json=args.json)
        if args.keys_command == "list":
            records = [credential.as_dict() for credential in store.list()]
            if args.json:
                print(json.dumps(records, sort_keys=True))
            elif not records:
                print("No ctxfs credentials.")
            else:
                for record in records:
                    scopes = ",".join(record["scopes"])
                    state = "active" if record["active"] else "inactive"
                    print(f"{record['key_id']}\t{record['name']}\t{scopes}\t{state}")
            return 0
        if args.keys_command == "revoke":
            credential = store.revoke(args.key_id)
            return _emit_key_metadata(credential.as_dict(), as_json=args.json)
        if args.keys_command == "rotate":
            issued = store.rotate(args.key_id)
            return _emit_issued_credential(issued, as_json=args.json)
    raise ValueError(f"Unknown keys command: {args.keys_command}")


def _emit_issued_credential(issued: IssuedCredential, *, as_json: bool) -> int:
    credential = issued.credential
    payload = {**credential.as_dict(), "token": issued.token}
    if as_json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"key id: {credential.key_id}")
        print(f"name: {credential.name}")
        print(f"token: {issued.token}")
        print("Store this token now. It will not be shown again.")
    return 0


def _emit_key_metadata(payload: dict[str, object], *, as_json: bool) -> int:
    if as_json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"key id: {payload['key_id']}")
        print(f"name: {payload['name']}")
        print(f"active: {str(payload['active']).lower()}")
    return 0


def _handle_login(args: argparse.Namespace) -> int:
    endpoint = resolve_ctxfs_endpoint_details(url=args.endpoint).endpoint
    if endpoint.startswith("unix://"):
        raise ValueError(
            "ctxfs login is only supported for HTTP(S) endpoints; "
            "Unix sockets use local peer trust."
        )
    api_key, should_save = _resolve_ctxfs_login_api_key(endpoint)
    if not api_key:
        raise ValueError(
            "Missing ctxfs API key. Set `CTXD_CTXFS_API_KEY` or run "
            "`ctxd login --endpoint URL` in an interactive terminal."
        )
    CtxfsClient(
        endpoint=endpoint, api_key=api_key, timeout=5.0
    ).validate_authentication()
    if should_save:
        save_ctxfs_api_key(api_key, endpoint=endpoint)
        print(f"Saved ctxfs authentication for {endpoint}.")
    else:
        print(f"ctxfs authentication is valid for {endpoint}.")
    return 0


def _handle_logout(args: argparse.Namespace) -> int:
    endpoint = resolve_ctxfs_endpoint_details(url=args.endpoint).endpoint
    clear_ctxfs_api_key(endpoint=endpoint)
    print(f"Cleared stored ctxfs authentication for {endpoint}.")
    return 0


def _handle_status(args: argparse.Namespace) -> int:
    resolution = resolve_ctxfs_endpoint_details(url=args.endpoint)
    if resolution.managed_local:
        ensure_started()
    endpoint = resolution.endpoint
    CtxfsClient(endpoint=endpoint, timeout=5.0).status()
    print(f"ctxfs endpoint: {endpoint}")
    print("ctxfs healthy: true")
    return 0


def _handle_endpoint(args: argparse.Namespace) -> int:
    if args.endpoint_command == "set":
        endpoint = resolve_ctxfs_endpoint_details(url=args.url).endpoint
        CtxfsClient(endpoint=endpoint, timeout=2.0).validate_authentication()
        save_ctxfs_endpoint(endpoint)
        print(f"ctxfs endpoint set to {endpoint}.")
        return 0
    if args.endpoint_command == "status":
        args.endpoint = None
        return _handle_status(args)
    raise ValueError("Unsupported endpoint command.")


def _handle_machine(args: argparse.Namespace) -> int:
    if args.machine_command == "configure":
        description = _description_argument(args)
        identity = configure_machine(name=args.name, description=description)
        status = machine_status()
        payload = status.as_dict()
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"machine name: {identity.name}")
            print(f"machine description: {identity.description or ''}")
            print(f"metadata sync: {status.metadata_sync}")
        return 0

    if args.machine_command == "status":
        return _emit_machine_status(
            machine_status(endpoint=args.endpoint), as_json=args.json
        )

    if args.machine_command == "register":
        description = _description_argument(args)
        if args.name is not None or description is not unchanged_description():
            configure_machine(name=args.name, description=description)
        endpoint = resolve_ctxfs_endpoint_details(url=args.endpoint).endpoint
        enrollment_token = _resolve_enrollment_token(endpoint)
        status, recovered = register_machine(
            endpoint=endpoint, enrollment_token=enrollment_token
        )
        payload = {**status.as_dict(), "registration_result": "recovered" if recovered else "created"}
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"machine registration: {payload['registration_result']}")
            print(f"machine id: {status.machine_id}")
            print(f"machine key: {status.machine_key}")
            print(f"ctxfs endpoint: {status.endpoint}")
        return 0
    raise ValueError("Unsupported machine command.")


def _description_argument(args: argparse.Namespace) -> str | None | object:
    if args.clear_description:
        return None
    if args.description is not None:
        return args.description
    return unchanged_description()


def _resolve_enrollment_token(endpoint: str) -> str | None:
    if endpoint.startswith("unix://"):
        return None
    value = os.getenv("CTXD_CTXFS_ENROLLMENT_TOKEN")
    if value and value.strip():
        return value.strip()
    if sys.stdin.isatty():
        try:
            value = getpass.getpass("ctxfs enrollment token: ")
        except (EOFError, KeyboardInterrupt):
            value = ""
        if value and value.strip():
            return value.strip()
    raise ValueError(
        "Missing ctxfs enrollment token. Set `CTXD_CTXFS_ENROLLMENT_TOKEN` "
        "or run in an interactive terminal."
    )


def _emit_machine_status(status, *, as_json: bool) -> int:
    payload = status.as_dict()
    if as_json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"machine registration: {status.registration_state}")
        print(f"machine name: {status.name}")
        print(f"machine description: {status.description or ''}")
        print(f"local registration id: {status.local_registration_id}")
        print(f"ctxfs endpoint: {status.endpoint}")
        print(f"metadata sync: {status.metadata_sync}")
        if status.machine_id is not None:
            print(f"machine id: {status.machine_id}")
            print(f"machine key: {status.machine_key}")
    return 0


def _resolve_ctxfs_login_api_key(endpoint: str) -> tuple[str | None, bool]:
    env_api_key = os.getenv("CTXD_CTXFS_API_KEY")
    if env_api_key and env_api_key.strip():
        return env_api_key.strip(), False
    try:
        stored_api_key = resolve_ctxfs_api_key(endpoint=endpoint)
    except CtxdError:
        stored_api_key = None
    if stored_api_key:
        return stored_api_key, False
    if not sys.stdin.isatty():
        return None, False
    try:
        value = getpass.getpass("ctxfs API key: ")
    except (EOFError, KeyboardInterrupt):
        return None, False
    return (value.strip(), True) if value and value.strip() else (None, False)


def _handle_folders(args: argparse.Namespace) -> int:
    if args.folders_command == "add":
        folder = add_folder(args.path, name=args.name)
        print(f"Added folder {folder.name}: {folder.path}")
        return 0

    if args.folders_command == "list":
        folders = list_folders()
        if not folders:
            print("No folders configured.")
            return 0
        for folder in folders:
            print(f"{folder.name}\t{folder.path}")
        return 0

    if args.folders_command == "remove":
        folder = remove_folder(args.name)
        print(f"Removed folder {folder.name}.")
        return 0

    raise ValueError("Unsupported folders command.")


def _handle_tracker(args: argparse.Namespace) -> int:
    if args.tracker_command == "start":
        state = tracker_start()
        print("Tracker started." if state["running"] else "Tracker not running.")
        print(f"Folders: {state['folders']}")
        return 0

    if args.tracker_command == "stop":
        state = tracker_stop()
        print("Tracker stopped." if not state["running"] else "Tracker still running.")
        return 0

    if args.tracker_command == "status":
        state = tracker_status()
        print(f"tracker running: {state['running']}")
        print(f"tracker pid: {state['pid']}")
        print(f"folders: {state['folders']}")
        return 0

    raise ValueError("Unsupported tracker command.")


def _handle_files(args: argparse.Namespace, client: Client) -> int:
    if args.files_command == "tree":
        result = client.files.tree(
            try_resolve_named_path(args.prefix),
            depth=args.depth,
            limit=args.limit,
        )
        return _emit_json_model(result)
    if args.files_command == "ls":
        result = client.files.ls(try_resolve_named_path(args.path), limit=args.limit)
        return _emit_json_model(result)
    if args.files_command == "glob":
        prefix = try_resolve_named_path(args.prefix) if args.prefix else ""
        pattern = try_resolve_named_path(args.pattern)
        if prefix and pattern == args.pattern:
            pattern = f"{prefix.rstrip('/')}/{pattern.lstrip('/')}"
        result = client.files.glob(
            pattern,
            prefix=prefix,
            limit=args.limit,
        )
        return _emit_json_model(result)
    if args.files_command == "grep":
        result = client.files.grep(
            args.pattern,
            prefix=try_resolve_named_path(args.prefix) if args.prefix else "",
            limit=args.limit,
        )
        return _emit_json_model(result)
    if args.files_command == "stat":
        result = client.files.stat(try_resolve_named_path(args.path))
        return _emit_json_model(result)
    if args.files_command == "read-lines":
        result = client.files.read_lines(
            try_resolve_named_path(args.path),
            start_line=args.start,
            end_line=args.end,
        )
        if args.json:
            return _emit_json_model(result)
        print("\n".join(result.lines))
        if not result.complete:
            print(
                f"Warning: output truncated by {result.stopped_by or 'limit'}.",
                file=sys.stderr,
            )
        return 0
    if args.files_command == "read":
        result = client.files.read(
            try_resolve_named_path(args.path),
            max_bytes=args.max_bytes,
        )
        if args.json:
            return _emit_json_model(result)
        print(result.text, end="" if result.text.endswith("\n") else "\n")
        if not result.complete:
            print(
                f"Warning: output truncated by {result.stopped_by or 'limit'}.",
                file=sys.stderr,
            )
        return 0

    raise ValueError("Unsupported files command.")


def _emit_json_model(result) -> int:
    print(json.dumps(result.model_dump(), indent=2, sort_keys=True))
    return 0
