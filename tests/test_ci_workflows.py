from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_docs_changes_are_validated_by_landing_before_required_ci_passes() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yaml").read_text()

    assert "git diff --quiet \"$BASE_SHA...$HEAD_SHA\" -- docs/public" in workflow
    assert "steps.public_docs.outputs.changed == 'true'" in workflow
    assert "repositories: ctxd-landing" in workflow
    assert "gh workflow run sync-docs.yml" in workflow
    assert '-f ctxd_commit="$CTXD_COMMIT"' in workflow
    assert '-f request_id="$REQUEST_ID"' in workflow
    assert "-f validate_only=true" in workflow
    assert "gh run watch \"$run_id\"" in workflow
    assert "--exit-status" in workflow
