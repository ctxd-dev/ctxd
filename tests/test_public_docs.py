import json
from pathlib import Path


PUBLIC_DOCS = Path(__file__).parents[1] / "docs" / "public"


def test_public_docs_manifest_is_complete_and_safe() -> None:
    manifest = json.loads((PUBLIC_DOCS / "manifest.json").read_text())

    assert manifest["schemaVersion"] == 1
    files = manifest["files"]
    assert files == sorted(set(files))
    assert all(Path(name).suffix == ".md" and ".." not in Path(name).parts for name in files)

    actual = sorted(
        path.relative_to(PUBLIC_DOCS).as_posix()
        for path in PUBLIC_DOCS.rglob("*.md")
        if path.name != "README.md"
    )
    assert files == actual


def test_public_docs_have_starlight_frontmatter() -> None:
    manifest = json.loads((PUBLIC_DOCS / "manifest.json").read_text())

    for name in manifest["files"]:
        text = (PUBLIC_DOCS / name).read_text()
        assert text.startswith("---\n"), name
        assert "\ntitle:" in text.split("---", 2)[1], name


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
