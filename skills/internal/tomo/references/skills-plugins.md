# Skills, tool assignments, and plugins

Use this for package installation, discovery, activation, per-agent capability
selection, plugin enablement, or improving reusable procedures.

## Distinguish the layers

| Layer | Meaning |
| --- | --- |
| Installed skill files | Procedure and references exist in a discovery root |
| Skill catalog enabled state | The discovered package is enabled globally |
| Agent skill assignment | The package is linked to that configured agent |
| Agent tool selection | Actual callable tools permitted for that agent |
| Plugin enabled state | Pages, tools, hooks, and packaged skills are active |
| MCP server/item state | External capability availability; separate from agent selection |

A skill describes how to work; loading it does not execute scripts, grant tools,
install dependencies, or authenticate a service. Use `agent_info` and current
runtime catalogs to determine callable capabilities.

## Install and discover packages

```bash
tomo skills list
tomo skills install /absolute/skill-directory
tomo skills sync
tomo config skills list --json
tomo config skills update <actual-skill-id> --set enabled=true --json
```

Discovery roots, precedence, and filesystem paths are in [config](config.md) and
[CLI lifecycle](cli.md). Library copies are managed; external packages stay in
their source roots. Bundled `tomo` wins over a duplicate library/external ID;
installing another package with the same ID does not replace it. Changing a
catalog description is not editing the package's source `SKILL.md` and can be
superseded by the next sync.

At runtime, use `list_skills(query=..., offset=..., limit=...)` when available;
follow pagination and load the selected package with `use_skill`. Load supporting
references with `use_skill(skill_id="...", file="references/...")`, not a remote
workplace read of a coordinator library path.

## Select tools and linked skills

```bash
tomo config agent-tools show <agent-id> --json
tomo config agent-skills show <agent-id> --json
tomo config agent-tools update <agent-id> --data @/absolute/tools.json --json
tomo config agent-skills update <agent-id> --data @/absolute/skills.json --json
```

Tools payload: `{"enabled":{"bash":true,"read_file":true}}`.
Skills payload: `{"skill_ids":["tomo"]}`. These are examples, not a recommended
replacement for every agent's existing capabilities. Build the complete intended
selection from its current state; saves replace selections and can remove other
access. Keep unrelated assignments. Unknown IDs fail.

Global skill/server/item enablement and per-agent selection are separate. Enabling
an MCP tool for an agent cannot override a disabled item/server. Use the actual
runtime tool ID/schema rather than constructing names from display labels.

## Improve a reusable skill

Use `manage_skill` for library skills when that runtime tool is enabled. Supported
actions are `create`, `edit`, `patch`, `write_file`, and `delete`. Patch an existing
class-level procedure when possible. `write_file` adds supporting references,
templates, scripts, or assets; link them from the entrypoint so the agent can find
them. Bundled skills are maintained in the repository, not overwritten by a
library mutation.

Keep frontmatter's description specific and short enough for catalog discovery.
Put purpose, important decisions, and reference routing in `SKILL.md`; put long
mode-specific procedures in references. Preserve actual limitations, provide
real command shapes, and distinguish examples from IDs/credentials to execute.
Capture a demonstrated reusable fix; do not turn a temporary failure or task log
into a universal rule. Validate discovery and reference loading after editing.

## Manage live plugins

Use the `plugin_manager` agent tool to install, enable, disable, reload, update, and
list trusted plugins without restarting Tomo. Read the `plugin-development`
skill when creating a plugin. Plugins can add pages, domain tools, static assets,
turn-end hooks, and packaged usage skills. Token Monitor, Task Board, and Money
live in the separate official `tomo-plugins` repository. Use `plugin-management`
for lifecycle and catalog operations; use `plugin-publishing` for releases or
community submissions.

The CLI contacts the running server with an administrator `TOMO_API_KEY`:

```bash
tomo plugins list
tomo plugins install /absolute/server/path/to/plugin
tomo plugins enable <plugin-id>
tomo plugins reload <plugin-id>
tomo plugins outdated
tomo plugins update <plugin-id>
tomo plugins disable <plugin-id>
```

Open `/extensions` for management, or `/plugins/<id>/` for a plugin page.
Plugin tools use `plugin__<id>__<tool>` names and normal agent assignments.
Disabled plugins have no active pages, tools, or packaged skills. Usage skills
have IDs `plugin__<plugin-id>__<skill-id>` and are loaded with `use_skill`. Plugin data survives reload and
uninstall. There is no `tomo config modules` resource or old module API.

Home (`/`) shows each enabled plugin as a room card in the 間 Rooms grid and
in the rail's "Rooms" group. Plugins that register `api.home_card(handler)` show
live per-user status (metric, stats, charts, series, donut, gauge, heatmap,
tables, steps, status bands, kanban columns, agenda, checklist, app tiles,
notice, image, actions); others get a door
card linking to their first page. `api.starter(label, prompt)` adds composer
chips. Cards are typed JSON rendered by core, never plugin HTML. Read
`use_skill(skill_id="plugin-development", file="references/home.md")` before
adding or changing a card. Users choose individual widgets with Home → Add widget, then reorder, remove,
restore, or resize them with Home → Arrange and the widget picker;
that layout is per user (`PUT /api/home/layout`) and is not plugin state.

Git branch updates compare source commits; version bumps are optional. Tags and
commit references are pinned. Check updates in Installed; use Update to download
and apply the selected commit live. Local sources use Reload after edits.
