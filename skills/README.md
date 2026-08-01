# Installable skill packages

Skills are directories containing a ``SKILL.md`` (agentskills.io style)
with optional YAML frontmatter (`name`, `description`, `version`).

## Discovery roots

| Root | Role |
|------|------|
| `$TOMO_HOME/library/skills` | Managed installs (writable) |
| `~/.agents/skills` | Shared user skills (read-only discover) |
| `~/.agent/skills` | Alternate shared path (read-only) |
| `~/.tomo/skills` | Peer of `$TOMO_HOME` for drop-in skills |
| `~/.claude/skills` | Claude Code skills (often symlinks into `.agents`) |

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
- ``use_skill`` — returns the skill body; optional ``file`` loads ``references/…`` (etc.) from the package without workplace ``read_file``
- ``manage_skill`` — create / edit / patch / delete library skills (active learning)

## Active learning

When **Settings → Learning loop** is on, Tomo may run a background review after
eligible multi-step turns. The reviewer can call ``remember`` and ``manage_skill``
to distill facts and class-level playbooks. Agents can also call those tools
mid-turn. Skills stay inspectable files under ``$TOMO_HOME/library/skills``.
