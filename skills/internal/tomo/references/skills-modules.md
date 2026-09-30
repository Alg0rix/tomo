# Skills, tool assignments, and modules

Use this for package installation, discovery, activation, per-agent capability
selection, module enablement, or improving reusable procedures.

## Distinguish the layers

| Layer | Meaning |
| --- | --- |
| Installed skill files | Procedure and references exist in a discovery root |
| Skill catalog enabled state | The discovered package is enabled globally |
| Agent skill assignment | The package is linked to that configured agent |
| Agent tool selection | Actual callable tools permitted for that agent |
| Module enabled state | A registered extension is enabled; not a skill package |
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

## Configure modules

```bash
tomo config modules list --json
tomo config modules show <module-id> --json
tomo config modules update <module-id> --set enabled=true --json
```

Generic module updates support `enabled`, `name`, `description`, and `version`.
They do not install a plugin or accept an arbitrary `config` object. Inspect an
extension's actual UI/routes/docs for module-specific operations; do not invent
a generic CLI endpoint. Apply runtime lifecycle only when the module requires it
and verify its actual behavior. Disabling a module does not delete user artifacts
or unregister an unrelated skill.
