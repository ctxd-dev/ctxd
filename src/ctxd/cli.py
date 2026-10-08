"""Public command-line interface for ctxd."""

import argparse
import getpass
import json
import os
import re
import sys
import webbrowser
from typing import Sequence

from ctxd import Client, CtxdError
from ctxd._metadata import SDK_VERSION
from ctxd.config import (
    clear_api_key,
    resolve_api_key,
    resolve_backend,
    save_api_key,
    save_backend,
)


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
        if args.command == "install-app":
            return _handle_install_app(args)
        if args.command == "config":
            return _handle_config(args)

        client = Client(backend=getattr(args, "backend", None))

        if args.command == "search":
            kwargs = _ctxfs_search_kwargs(args)
            result = client.search(_normalize_search_query(args.query), **kwargs)
            return _emit_result(result.model_dump(), as_json=True)
        if args.command == "fetch":
            result = client.fetch_document(args.document_uid)
            return _emit_result(result.model_dump(), as_json=args.json)
        if args.command == "profile":
            result = client.get_profile()
            return _emit_result(result.model_dump(), as_json=args.json)
        if args.command == "files":
            return _handle_files(args, client)
    except (CtxdError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    parser.error(f"Unknown command: {args.command}")
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ctxd",
        description=(
            "Search and fetch content from your ctxd-connected apps, manage CLI "
            "authentication, and inspect indexed data."
        ),
        epilog=(
            "Examples:\n"
            "  ctxd login\n"
            "  ctxd install-app\n"
            "  ctxd search text:deployment application:slack\n"
            "  ctxd fetch doc-123 --json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"ctxd {SDK_VERSION}",
        help="Print the installed ctxd version and exit.",
    )
    parser.add_argument(
        "--backend",
        choices=("hosted", "ctxfs"),
        help="Backend to use for this command. Defaults to CTXD_BACKEND, config, then hosted.",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        metavar="<command>",
        title="commands",
        required=True,
    )

    subparsers.add_parser(
        "login",
        help="Store an API key for future CLI and SDK calls.",
        description="Prompt for a ctxd API key and store it for future CLI and SDK calls.",
    )
    subparsers.add_parser(
        "logout",
        help="Remove the stored API key from this machine.",
        description="Clear the locally stored ctxd API key.",
    )
    subparsers.add_parser(
        "status",
        help="Show whether ctxd authentication is configured.",
        description="Check whether an API key is available from the environment or stored credentials.",
    )

    install_app_parser = subparsers.add_parser(
        "install-app",
        help="Open the app installation page for connecting Slack, Google Drive, and more.",
        description=(
            "Open the ctxd app installation page, where you can connect integrations "
            "such as Slack, Google Drive, GitHub, and Google Calendar."
        ),
    )
    install_app_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the installation URL without opening a browser.",
    )

    config_parser = subparsers.add_parser(
        "config",
        help="Get or set ctxd CLI configuration.",
        description="Get or set ctxd CLI configuration.",
    )
    config_subparsers = config_parser.add_subparsers(
        dest="config_command",
        metavar="<config-command>",
        required=True,
    )
    config_get_parser = config_subparsers.add_parser(
        "get",
        help="Get a configuration value.",
    )
    config_get_parser.add_argument("key", choices=("backend",))
    config_set_parser = config_subparsers.add_parser(
        "set",
        help="Set a configuration value.",
    )
    config_set_parser.add_argument("key", choices=("backend",))
    config_set_parser.add_argument("value")

    search_parser = subparsers.add_parser(
        "search",
        help="Search indexed app content and print JSON results.",
        description=(
            "Search indexed app content using ctxd DSL. Search output is always JSON. "
            "The query can be quoted or passed as separate tokens."
        ),
        epilog=(
            "Examples:\n"
            "  ctxd search text:deployment application:slack\n"
            '  ctxd search "text:deployment application:slack"'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    search_parser.add_argument(
        "query",
        nargs="+",
        metavar="QUERY",
        help="Search query or DSL tokens, for example: text:deployment application:slack.",
    )
    search_parser.add_argument(
        "--prefix",
        help="For ctxfs backend searches, restrict grep to this path prefix.",
    )
    search_parser.add_argument(
        "--limit",
        type=int,
        help="For ctxfs backend searches, limit grep matches.",
    )

    fetch_parser = subparsers.add_parser(
        "fetch",
        help="Fetch one indexed document by document UID.",
        description="Fetch a single indexed document by document UID.",
    )
    fetch_parser.add_argument("document_uid", help="Document UID returned by search.")
    fetch_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full document response as JSON.",
    )

    profile_parser = subparsers.add_parser(
        "profile",
        help="Show connected integrations and indexed file tree.",
        description="Show integration access plus the indexed file tree for the authenticated user.",
    )
    profile_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the profile response as JSON.",
    )

    files_parser = subparsers.add_parser(
        "files",
        help="Read path-oriented content from the active backend.",
        description="Read path-oriented content from the active backend.",
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

    return parser


def _normalize_search_query(query_tokens: Sequence[str]) -> str:
    normalized_tokens = [
        _quote_shell_stripped_text_token(token) for token in query_tokens
    ]
    return " ".join(normalized_tokens)


def _quote_shell_stripped_text_token(token: str) -> str:
    if not token.lower().startswith("text:"):
        return token

    value = token[5:]
    if not value or not re.search(r"\s", value):
        return token

    stripped_value = value.strip()
    if (
        stripped_value.startswith(("\"", "'", "("))
        or stripped_value.endswith(("\"", "'", ")"))
    ):
        return token

    escaped_value = stripped_value.replace("\\", "\\\\").replace('"', '\\"')
    return f'text:"{escaped_value}"'


def _handle_login(args: argparse.Namespace) -> int:
    del args
    api_key, should_save = _resolve_login_api_key()
    if not api_key:
        raise ValueError(
            "Missing API key. Set `CTXD_API_KEY` or run `ctxd login` in an interactive terminal."
        )

    Client(api_key=api_key, backend="hosted").get_profile()

    if should_save:
        save_api_key(api_key)
        print("Saved API key authentication.")
    else:
        print("API key authentication is valid.")
    return 0


def _handle_logout(args: argparse.Namespace) -> int:
    del args
    clear_api_key()
    print("Cleared stored ctxd API key.")
    return 0


def _handle_status(args: argparse.Namespace) -> int:
    del args
    try:
        api_key = resolve_api_key()
    except CtxdError as exc:
        print(f"Not authenticated: {exc}", file=sys.stderr)
        return 1

    if not api_key:
        print(
            "Not authenticated: Missing API key. Set `CTXD_API_KEY` or run `ctxd login`.",
            file=sys.stderr,
        )
        return 1

    print("Authenticated to ctxd.")
    return 0


def _resolve_login_api_key() -> tuple[str | None, bool]:
    env_api_key = os.getenv("CTXD_API_KEY")
    if env_api_key and env_api_key.strip():
        return env_api_key.strip(), False

    try:
        stored_api_key = resolve_api_key()
    except CtxdError:
        stored_api_key = None
    if stored_api_key:
        return stored_api_key, False

    prompted_api_key = _prompt_api_key()
    if prompted_api_key:
        return prompted_api_key, True

    return None, False


def _prompt_api_key() -> str | None:
    if not sys.stdin.isatty():
        return None

    try:
        api_key = getpass.getpass("ctxd API key: ")
    except (EOFError, KeyboardInterrupt):
        return None

    if api_key and api_key.strip():
        return api_key.strip()
    return None


def _handle_install_app(args: argparse.Namespace) -> int:
    auth_url = "https://app.ctxd.dev/knowledge-base/add-application"

    print("To install an app, go to:")
    print(auth_url)

    if not args.no_browser:
        webbrowser.open(auth_url)
    return 0


def _handle_config(args: argparse.Namespace) -> int:
    if args.config_command == "get" and args.key == "backend":
        print(resolve_backend())
        return 0

    if args.config_command == "set" and args.key == "backend":
        backend = resolve_backend(args.value)
        save_backend(backend)
        print(f"Backend set to {backend}.")
        return 0

    raise ValueError("Unsupported config command.")


def _ctxfs_search_kwargs(args: argparse.Namespace) -> dict:
    kwargs = {}
    if getattr(args, "prefix", None):
        kwargs["prefix"] = args.prefix
    if getattr(args, "limit", None) is not None:
        kwargs["limit"] = args.limit
    return kwargs


def _handle_files(args: argparse.Namespace, client: Client) -> int:
    if args.files_command == "tree":
        result = client.files.tree(args.prefix, depth=args.depth, limit=args.limit)
        return _emit_json_model(result)
    if args.files_command == "ls":
        result = client.files.ls(args.path, limit=args.limit)
        return _emit_json_model(result)
    if args.files_command == "glob":
        result = client.files.glob(args.pattern, prefix=args.prefix, limit=args.limit)
        return _emit_json_model(result)
    if args.files_command == "stat":
        result = client.files.stat(args.path)
        return _emit_json_model(result)
    if args.files_command == "read-lines":
        result = client.files.read_lines(
            args.path,
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

    raise ValueError("Unsupported files command.")


def _emit_json_model(result) -> int:
    print(json.dumps(result.model_dump(), indent=2, sort_keys=True))
    return 0


def _emit_result(payload: dict, *, as_json: bool) -> int:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1 if _payload_has_error(payload) else 0

    if "results" in payload:
        error = payload.get("error")
        dsl_parse_error = payload.get("dsl_parse_error")
        if error:
            print(f"Error: {error}")
        if dsl_parse_error:
            print(f"DSL parse error: {dsl_parse_error}")
        if not payload["results"] and not error and not dsl_parse_error:
            print("No results found.")
        for item in payload["results"]:
            print(f"- {item['title']} [{item['id']}]")
            print(f"  URL: {item['url']}")
            if item.get("text"):
                print(f"  Text: {item['text']}")
        return 1 if error or dsl_parse_error else 0

    if "integration_access" in payload:
        print(payload["integration_access"])
        if payload.get("file_tree"):
            print()
            print(payload["file_tree"])
        return 0

    if payload.get("error"):
        print(f"Error: {payload['error']}")
        return 1

    title = payload.get("title") or payload.get("id") or "Document"
    print(title)
    if payload.get("url"):
        print(payload["url"])
    if payload.get("text"):
        print()
        print(payload["text"])
    return 0


def _payload_has_error(payload: dict) -> bool:
    if payload.get("error"):
        return True
    return bool(payload.get("dsl_parse_error"))
