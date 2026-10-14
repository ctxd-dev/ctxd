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


def test_cli_docs_only_list_supported_local_environment_variables() -> None:
    text = (PUBLIC_DOCS / "cli.md").read_text()

    assert "CTXD_CONFIG_PATH" in text
    assert "CTXD_BACKEND" in text
    assert "CTXD_CTXFS_SOCKET" in text
    assert "CTXD_CTXFS_URL" in text
    assert "CTXD_LOCAL_HOME" not in text
    assert "CTXD_LOCAL_CTXFS_HOST" not in text
    assert "CTXD_LOCAL_CTXFS_PORT" not in text
