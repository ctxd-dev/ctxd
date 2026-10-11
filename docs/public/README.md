# Public documentation

This directory is the source of truth for the user-facing documentation rendered
at `ctxd.dev/docs`. Edit these files in the same pull request as any public CLI,
API, security, or behavior change.

After a change merges to `main`, `dispatch-docs.yml` sends the exact commit SHA to
`ctxd-landing`. That repository copies this directory, regenerates documentation
visual baselines, runs its site checks, and maintains an auto-merge pull request.
A scheduled reconciliation in `ctxd-landing` catches missed dispatches.

Do not edit the generated copies in `ctxd-landing/src/content/docs/docs` directly.
The sync process replaces that directory from this one.

`manifest.json` is an allowlist. Add or remove a page there in the same change as
the Markdown file. Navigation and route changes in `ctxd-landing` still require a
coordinated landing change.
