# Contributing

## Bumping the Package Version

The package version is defined in `pyproject.toml` in two places:

- `project.version`
- `tool.bumpversion.current_version`

PyPI does not allow re-publishing the same version. Any PR that should publish a
new package must bump both values to the next version before merging.

### Manual Version Bump

For a patch release, update `pyproject.toml`:

```toml
[project]
version = "0.1.14"

[tool.bumpversion]
current_version = "0.1.14"
```

Then run the test suite:

```bash
uv run pytest tests -v
```

Commit the version bump with the code change:

```bash
git add pyproject.toml
git commit -m "Bump version to 0.1.14"
```

### Using bump-my-version

This repository includes `tool.bumpversion` configuration. If
`bump-my-version` is installed, you can bump the patch version with:

```bash
uv tool run bump-my-version bump patch
```

Review the resulting `pyproject.toml` change before committing it.

### Publishing

Publishing is handled by GitHub Actions. Pull requests run tests and build the
package, but do not publish to PyPI.

After a PR is merged, a push to `main` runs the full test job. If the tests pass,
the publish job checks whether the current version already exists on PyPI:

- If the version does not exist, the package is published.
- If the version already exists, publishing is skipped.

Do not push release tags to publish. Releases are published from `main` after
the merge commit passes CI.
