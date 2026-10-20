from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_public_repo_workflows_have_no_landing_repository_credentials() -> None:
    workflows = "\n".join(
        path.read_text() for path in (ROOT / ".github" / "workflows").glob("*.y*ml")
    )

    assert "CTXD_DOCS_APP" not in workflows
    assert "ctxd-landing" not in workflows
    assert "create-github-app-token" not in workflows
