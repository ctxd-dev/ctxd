"""Public command-line interface for ctxd."""

import argparse
import getpass
import json
import os
import re
import shlex
import sys
import webbrowser
from typing import Sequence

from ctxd import Client, CtxdError
from ctxd._metadata import SDK_VERSION
from ctxd.config import clear_api_key, resolve_api_key, save_api_key


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

        client = Client()

        if args.command == "search":
            query = _normalize_search_query(args.query)
            _validate_search_query(query)
            result = client.search(query)
            return _emit_result(result.model_dump(), as_json=True)
        if args.command == "fetch":
            result = client.fetch_document(args.document_uid)
            return _emit_result(result.model_dump(), as_json=args.json)
        if args.command == "profile":
            result = client.get_profile()
            return _emit_result(result.model_dump(), as_json=args.json)
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
            "  ctxd search application:slack text:deployment\n"
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

    search_parser = subparsers.add_parser(
        "search",
        help="Search indexed app content and print JSON results.",
        description=(
            "Search indexed app content using ctxd DSL. Search output is always JSON. "
            "The query can be quoted or passed as separate tokens."
        ),
        epilog=(
            "Examples:\n"
            "  ctxd search text:deployment\n"
            "  ctxd search application:slack text:deployment\n"
            "  ctxd search application:google_drive text:incident response\n"
            '  ctxd search application:google_drive \'text:"incident response"\'\n'
            "\n"
            "Query format:\n"
            "  application:<app> text:<terms>\n"
            "  application:<app> is optional; omit it to search all connected apps.\n"
            "  If application:<app> is present, it must be the first token.\n"
            "  Only one application filter is supported.\n"
            '  Use text:"a b" when your shell preserves the quotes and you need exact text.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    search_parser.add_argument(
        "query",
        nargs="+",
        metavar="QUERY",
        help="Search query tokens, for example: application:slack text:deployment.",
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


def _validate_search_query(query: str) -> None:
    tokens = _split_search_query(query)
    _validate_application_filters(tokens)
    _validate_boolean_operators(tokens)


def _split_search_query(query: str) -> list[str]:
    try:
        return shlex.split(query)
    except ValueError:
        return query.split()


def _validate_application_filters(tokens: Sequence[str]) -> None:
    application_positions: list[int] = []
    for index, token in enumerate(tokens):
        if not token.lower().startswith("application:"):
            continue

        value = token[len("application:") :]
        if not value:
            raise ValueError(
                "Invalid search query: application: requires an app name, for example application:slack."
            )
        if value.startswith("("):
            raise ValueError(
                "Invalid search query: grouped application filters are not supported. "
                "Use one leading application filter, for example application:google_drive text:onboarding."
            )
        application_positions.append(index)

    if not application_positions:
        return
    if application_positions[0] != 0:
        raise ValueError(
            "Invalid search query: application: must be the first token, "
            "for example application:slack text:deployment."
        )
    if len(application_positions) > 1:
        raise ValueError(
            "Invalid search query: only one application filter is supported. "
            "Omit application: to search all connected apps."
        )


def _validate_boolean_operators(tokens: Sequence[str]) -> None:
    operators = {"AND", "OR"}
    for token in tokens:
        if token.upper() in operators:
            raise ValueError(
                "Invalid search query: AND/OR clauses are not supported. "
                "Use application:<app> text:<terms>, or omit application:<app> to search all apps."
            )


def _handle_login(args: argparse.Namespace) -> int:
    del args
    api_key, should_save = _resolve_login_api_key()
    if not api_key:
        raise ValueError(
            "Missing API key. Set `CTXD_API_KEY` or run `ctxd login` in an interactive terminal."
        )

    Client(api_key=api_key).get_profile()

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
    if "results" in payload:
        return bool(payload.get("error") or payload.get("dsl_parse_error"))
    if payload.get("error"):
        return True
    if payload.get("dsl_parse_error"):
        return True
    return "error" in payload and _document_payload_is_unresolved(payload)


def _document_payload_is_unresolved(payload: dict) -> bool:
    return not any(payload.get(key) for key in ("id", "title", "text", "url"))
