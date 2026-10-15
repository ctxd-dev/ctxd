import json
from pathlib import Path


PUBLIC_DOCS = Path(__file__).parents[1] / "docs" / "public"


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


def test_cli_docs_only_list_supported_local_environment_variables() -> None:
    text = (PUBLIC_DOCS / "cli.md").read_text()

    assert "CTXD_CONFIG_PATH" in text
    assert "CTXD_BACKEND" in text
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
        "ctxd status",
        "ctxd folders repair-move",
        "ctxd version",
    )

    for path in PUBLIC_DOCS.rglob("*.md"):
        text = path.read_text()
        for command in removed_commands:
            assert command not in text, f"{path.name}: {command}"


def test_public_docs_use_the_supported_local_workflow() -> None:
    public_text = "\n".join(
        path.read_text() for path in PUBLIC_DOCS.rglob("*.md")
    )

    assert "ctxd backend set ctxfs" in public_text
    assert "ctxd files tree" in public_text
    assert "ctxd files grep" in public_text
    assert "POST /mcp" in public_text
    assert "ctxd-mcp" in public_text
    assert "~/.ctxd/local/ctxfs.sock" in public_text
    assert "curl " not in public_text
    assert "ctxd config set backend ctxfs" not in public_text
    assert "http://127.0.0.1:8765" not in public_text
    assert "macOS, Linux, or Windows" not in public_text
    assert "ctxd folders remove <ROOT_ID>" not in public_text


def test_overview_describes_optional_tracker_and_endpoint_ownership() -> None:
    text = (PUBLIC_DOCS / "overview.md").read_text()

    assert "optional **local tracker**" in text
    assert "ctxfs server\ncan run and serve its existing store" in text
    assert "Read through the ctxd CLI" in text
    assert "Starting the tracker ensures the ctxfs server is running" not in text
