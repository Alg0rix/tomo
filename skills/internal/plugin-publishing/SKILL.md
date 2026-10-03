---
name: plugin-publishing
description: Publish Tomo plugins or prepare community marketplace submissions.
---

# Publish a Tomo plugin

Apply when publication or marketplace submission is requested, not during every
local development task. Use the user's existing authorization; prepare source,
metadata, tests, and the exact destination before any unapproved remote mutation.
Do not infer permission to publish a private project from an install request.

Plugin code stays in its source repository. Official plugins live in
https://github.com/Alg0rix/tomo-plugins and its independent `marketplace.json`.
Community submissions are metadata in https://github.com/Alg0rix/tomo-marketplace;
author code stays in a public GitHub repo. Official plugins do not register in
the community catalog. Direct source installation needs no listing at all.

Read current repository instructions, `schema/marketplace.schema.json`,
`scripts/validate.py`, and listing examples from the target checkout. They are
the contract; do not copy BB's npm package schema, categories, or API names.

A listing contains `id`, `name`, `description`, `version`, `sdk_version: 1`,
`author`, optional named Lucide `icon`, and `source` with `type: git`, public
HTTPS GitHub `url`, `ref`, and `subdirectory`. IDs/version/SDK must match the
package manifest. Keep descriptions concrete; document actual pages, tools,
skills, data scope, dependencies, and operating constraints in the plugin repo.
Do not embed credentials, private URLs, arbitrary HTML, or raw SVG in metadata.

Tracking branch installs detect new commits without a version bump. Keep the
manifest and listing versions equal; bump both for a named release rather than
for every code edit. Tags/commits are pinned and do not track updates.

For an official plugin, update its source and independent catalog when its
listing metadata changes. For a
community plugin, add `plugins/<plugin-id>.json`, run
`python scripts/build_catalog.py`, then `python scripts/validate.py`. Commit the
listing and generated catalog together; open a submission PR when requested.
Marketplace validation reads metadata and never executes submitted plugin code.

Validate the plugin separately in a temporary runtime: pages/tools/skills, user
isolation, install-disabled/enable/reload/disable, and screenshots for visual
features. Run the package's tests. Pin a release tag or commit when reproducible
installation matters. Verify the remote source exists before listing it.

Report the source/commit, marketplace or PR URL, and validation results. Do not
claim an unmerged submission is discoverable from the main catalog.
