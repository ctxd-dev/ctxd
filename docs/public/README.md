# Public documentation

This directory is the source of truth for the user-facing documentation rendered
at `ctxd.dev/docs`. Edit these files in the same pull request as any public CLI,
API, security, or behavior change.

`ctxd-landing` periodically reads this public directory from `main`, regenerates
documentation visual baselines, runs its site checks, and maintains a
synchronization pull request. The landing repository owns its workflow and
credentials; this public repository has no access to the landing repository.

Do not edit the generated copies in `ctxd-landing/src/content/docs/docs` directly.
The sync process replaces that directory from this one.

`manifest.json` is the schema-v2 publication and navigation catalog. Each entry
declares its source file, public slug, navigation title, group, and reading
order. Add or remove a page there in the same change as the Markdown file.
Public route changes in `ctxd-landing` still require a coordinated landing
change; catalog discovery alone never authorizes a route.
