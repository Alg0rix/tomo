# Skills shipped by a plugin

Put each agent playbook in `skills/<skill-name>/SKILL.md` inside the plugin's
source package. No separate library install or SDK registration call is needed.

```yaml
---
name: money-management
description: Record transactions and review spending with the Money plugin.
---
```

Document when to use the plugin, actual tool names and argument units, which
actions write data, ownership rules, and useful workflows. Keep the entrypoint
short. Put substantial details in `references/`, linked from SKILL.md. Supporting
`references`, `templates`, `scripts`, and `assets` load through `use_skill`.
Reading a script does not execute it or grant new capabilities.

The runtime prefixes each skill ID:
`plugin__money__money-management` for plugin `money` and skill `money-management`.
Same-named skills from other plugins do not collide with it or core skills.
Use the catalog's actual namespaced ID. The source label is `plugin:money`.

Enabled plugins expose these skills through `list_skills`, `use_skill`, Skills,
and normal agent prompt discovery. Reload refreshes skill bodies/references;
disable and uninstall remove them from active discovery. Skills are read-only
plugin source: change them in the plugin repo and reload, not with `manage_skill`.
A failed reload keeps the previous running instance and its skill snapshot.

For example:
`use_skill(skill_id="plugin__money__money-management", file="references/ledger.md")`.
Plugin skill paths and support files must stay inside the package; symlinks
outside it and duplicate skill IDs are rejected. Prefix `plugin__` is reserved
for the runtime and should not be used for unrelated library skills.
