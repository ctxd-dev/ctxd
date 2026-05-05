# Contributing

Thanks for helping improve `ctxd`. This guide covers the local development
workflow, pull request expectations, and release notes specific to this
repository.

## Development Setup

Clone the repository, then install the project and development dependencies with
`uv`:

```bash
uv sync
```

Verify the CLI is available through the local environment:

```bash
uv run ctxd --version
```

This project supports Python 3.11 and 3.12.

## Running Tests

Run the test suite before opening a pull request:

```bash
uv run pytest tests -v
```

Add or update tests when changing SDK behavior, CLI behavior, configuration
handling, or error handling.

## Pull Requests

Keep pull requests focused on one change or closely related set of changes.
Before opening a PR:

- Create a branch for the change.
- Add or update tests for behavior changes.
- Update `README.md` or other docs for user-facing changes.
- Run `uv run pytest tests -v`.
- Mention any tests you could not run in the PR description.

Maintainers may ask for revisions before merging. Documentation-only PRs usually
do not need a package version bump.

## Bug Reports and Feature Requests

When filing a bug report, include:

- The installed `ctxd` version.
- Your Python version and operating system.
- The command or code snippet that reproduces the issue.
- Expected behavior and actual behavior.
- Relevant traceback or command output.

For feature requests, describe the use case, the current limitation, and the
interface affected, such as the Python SDK, CLI, MCP, or REST API.

## Security Reports

Do not open a public issue for security vulnerabilities, leaked credentials, or
bugs that could expose private data. Contact the maintainers privately with the
details and enough information to reproduce or assess the issue.

## Bumping the Package Version

The package version is defined in `pyproject.toml` in two places:

- `project.version`
- `tool.bumpversion.current_version`

PyPI does not allow re-publishing the same version. Any PR that should publish a
new package must bump both values to the next version before merging. Maintainers
may ask for a version bump when a change should be released to PyPI.

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
