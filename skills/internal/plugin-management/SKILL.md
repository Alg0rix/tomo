---
name: plugin-management
description: Discover, install, enable, reload, or remove trusted Tomo plugins.
---

# Manage live plugins

Use `plugin_manager` for agent operations. It requires an enabled administrator
account. List first when state or IDs are uncertain. Management changes affect
the running server; no restart is needed. Develop with `plugin-development`.

| Action | Arguments |
| --- | --- |
| list | none |
| install | path; optional ref and subdirectory for a GitHub source |
| enable / disable / reload / uninstall | id |
| marketplaces | none |
| search | query |
| marketplace_add | source |
| marketplace_refresh / marketplace_remove | marketplace id in id |

Sources are a server-local directory, `money@tomo-official`, or an HTTPS GitHub
repo URL. Direct Git installs may select a branch/tag/commit and subdirectory.
Installs are disabled until Enable; enabling/reloading executes trusted server
code. Use the authorization already given by the task, and do not treat metadata
discovery as permission to execute unrelated plugins. Dependencies are not
installed automatically. Private repositories are not supported.

Catalogs are metadata caches. Refresh before expecting new releases/listings.
Official `tomo-official` (Alg0rix/tomo-plugins) and community `tomo-community`
(Alg0rix/tomo-marketplace) are independent. Direct installation works without any
marketplace registration. Removing a custom marketplace does not uninstall its
plugins; built-in marketplace registrations cannot be removed.

Enable adds pages, tools, and packaged skills. Reload applies source/skill edits
at the same installed path; it does not download a new Git release. Disable
removes live capabilities. Uninstall unregisters the plugin and keeps its source
and data. Busy actions return a conflict: retry after current work finishes.
Failed reload keeps the prior running instance. Report errors as errors, not as
a request to restart Tomo. Consult runtime logs for activation failures.

Browser: `/extensions` Discover / Installed / Create, and
`/settings/marketplaces`. CLI uses a server administrator `TOMO_API_KEY`:

```sh
tomo plugins list
tomo plugins marketplaces refresh tomo-official
tomo plugins search money
tomo plugins install money@tomo-official
tomo plugins enable money
tomo plugins reload money
```

Use the actual plugin ID. Verify running/error state, page URLs, and new tools /
skills. Normal per-agent tool and skill settings remain separate; preserve other
selections. Domain playbooks are under namespaced `plugin__<id>__<skill>` IDs.
