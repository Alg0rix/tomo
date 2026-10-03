# Installable skill packages

Skills are directories containing a ``SKILL.md`` (agentskills.io style)
with optional YAML frontmatter (`name`, `description`, `version`).

## Discovery roots

| Root | Role |
|------|------|
| `<Tomo repo>/skills/internal` | Bundled skills (read-only) |
| `$TOMO_HOME/library/skills` | Managed installs (writable) |
| `~/.agents/skills` | Shared user skills (read-only discover) |
| `~/.agent/skills` | Alternate shared path (read-only) |
| `~/.tomo/skills` | Peer of `$TOMO_HOME` for drop-in skills |
| `~/.claude/skills` | Claude Code skills (often symlinks into `.agents`) |

Override external roots with ``TOMO_SKILLS_EXTERNAL_DIRS`` (colon-separated). Set
empty to disable external discovery.

## Internal skills

Bundled packages:

- [Tomo](internal/tomo/SKILL.md): operate and configure Tomo.
- [Plugin development](internal/plugin-development/SKILL.md): build pages, tools, and usage skills.
- [Plugin management](internal/plugin-management/SKILL.md): install and manage live plugins and catalogs.
- [Plugin publishing](internal/plugin-publishing/SKILL.md): release official plugins or submit community listings.
- [Documents](internal/documents/SKILL.md): create, edit, and style Word/PDF
  deliverables; includes design, embedded diagrams, proposal helpers, and page preview.

Agents load a Tomo topic with
`use_skill(skill_id="tomo", file="references/<file>")`.

| File | Topic |
|------|--------|
| `references/swarm.md` | Worker teams |
| `references/connector.md` | Tunnel workplaces and MCP |
| `references/workplaces.md` | Targeting a tunnel/SSH host in tool calls (`workplace=`, resolution order) |
| `references/config.md` | Home, env, secrets, models |
| `references/cli.md` | CLI lifecycle and skill packages |
| `references/configuration-cli.md` | Local configuration syntax, action support, schemas, reloads |
| `references/agents-models.md` | Agents, personas, LLM profiles, workplace scopes |
| `references/skills-plugins.md` | Packages, tool/skill assignments, reusable playbooks, plugins |
| `references/schedules.md` | Scheduled prompts, recurrence, pause/resume, run verification |
| `references/channels-settings.md` | Telegram, transcription, approvals, learning, general limits |
| `references/memory-knowledge.md` | Full memory lifecycle: scopes, capture, wikilinks/aliases, recall, edit/move/forget, extraction/consolidation, retention, backup/restore |
| `references/accounts-sessions.md` | Login accounts, API keys, sessions/history, saved/shared artifacts |
| `references/install.md` | Install paths: systemd, Docker, source, connector binary |

The catalog shows each skill's `description`, truncated at 80 characters
(`app/runtime/agent/skills_prompt.py`). Keep that line short enough to survive
the cut. A bundled id wins over a library or external package with the same id.

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

## Plugin-owned skills

Enabled plugins contribute `skills/<name>/SKILL.md` from their source package.
IDs use `plugin__<plugin-id>__<skill-id>`; that namespace is reserved. They are
read-only in skill management and refreshed through plugin reload. Disable or
uninstall removes their availability. Complete official examples live in
[tomo-plugins](https://github.com/Alg0rix/tomo-plugins).
