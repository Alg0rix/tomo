# Installable skill packages

Skills are directories containing a ``SKILL.md`` (agentskills.io / Hermes style)
with optional YAML frontmatter (`name`, `description`, `version`).

## Discovery roots

| Root | Role |
|------|------|
| `$TOMO_HOME/library/skills` | Managed installs (writable) |
| `~/.agents/skills` | Shared user skills (read-only discover) |
| `~/.agent/skills` | Alternate shared path (read-only) |

Override external roots with ``TOMO_SKILLS_EXTERNAL_DIRS`` (colon-separated). Set
empty to disable external discovery.

## CLI

```bash
tomo skills sync
tomo skills list
tomo skills install ./path/to/skill-dir
tomo skills uninstall <id>    # library installs only
```

## Agent tools

- ``list_skills`` — catalog (syncs first)
- ``use_skill`` — returns the skill body from disk
