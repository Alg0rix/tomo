---
name: plugin-development
description: Build Tomo plugins — pages, agent tools, Home cards, usage skills.
---

# Develop Tomo plugins

A Tomo plugin is a trusted Python package exposing synchronous `setup(api)` from
`plugin.py`. Develop the feature in its own source directory, then install,
enable, or reload it in the running server. Keep domain code out of Tomo core.
Working implementations live in https://github.com/Alg0rix/tomo-plugins;
Money is a two-page example with agent tools and private per-user storage.

Read only the references relevant to the work with
`use_skill(skill_id="plugin-development", file="references/<name>.md")`:

- [Quickstart](references/quickstart.md): package layout, manifest, and first page.
- [Pages and design](references/pages.md): routing, templates, assets, icons, sidebar.
- [Agent tools and data](references/tools-data.md): schemas, user isolation, persistence.
- [Home cards](references/home.md): per-user room card and composer starters on Home.
- [Plugin skills](references/plugin-skills.md): package usage guidance for agents.
- [Lifecycle and testing](references/testing.md): validation, live loop, rollback, hooks.
- [SDK contract](references/sdk.md): supported API signatures and limits.

Use `plugin-management` for discovery/install/lifecycle operations and
`plugin-publishing` when publication or marketplace submission is requested.
The installed SDK is the contract; inspect `app/plugins/sdk.py` if present,
not a BB TypeScript API. Tomo does not support provider registration, core UI
replacement, arbitrary client scripts, background-service management, or npm
plugins. Do not invent those surfaces.

Reuse Tomo's existing agents, models, permissions, navigation, and skill loader.
A domain plugin exposes tools; it does not create a second AI client. Respect
existing tool selections. Declare dependencies in requirements.txt or project.dependencies in
pyproject.toml. Use plugin_manager(action="sync_dependencies", id=...) before
Enable. uv resolves all plugins together into a persistent overlay; existing
package versions are preserved. Distribution names differ from imports: cv2
requires opencv-python-headless on a server. Source install never runs setup.

Source paths must exist on the Tomo server. A tunnel or SSH workplace may be a
different machine. Develop in a server-local workplace unless the user has
chosen a remote-source transfer workflow. Never restart Tomo just to apply a
plugin change. Verify the actual pages and tools before calling the task done.
