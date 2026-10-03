---
name: tomo
description: "Operate Tomo: agents, models, tools, jobs, memory, accounts, channels, and CLI."
version: 1.0
---

# Tomo

Use this skill to configure or operate this Tomo installation, connect machines,
configure agents/models/tools, manage schedules/accounts/channels/memory,
deliver session artifacts, or coordinate configured agents. Follow the user's
language and carry the requested task through its verification.

## Choose the execution path

- For configuration on the coordinator, use the local `tomo` CLI. It uses the
  existing database and needs no HTTP request, API key, or interactive UI.
- For work on a connected machine, discover it with `list_workplaces` and use
  the available runtime tools on its actual workplace ID. Only `bash` takes a
  per-call `workplace` argument; load `references/workplaces.md` for the other
  tools. The CLI registry does not observe the server's live tunnel connection.
- For connector installation, create/reuse the tunnel on the coordinator, then
  SSH and install/pair on the target with ordinary terminal commands. SSH does
  not need a separate Tomo installer abstraction.
- For one configured peer, use `delegate` when available. For a worker team,
  load the swarm reference and use the actual enabled swarm tools.

Confirm where a command will execute. A shell tool can already be routed to a
remote workplace: `tomo config` must run on the coordinator with its OS account
and `TOMO_HOME`/`TOMO_DB_PATH`; `tomo-connector` runs on the target. A Docker
coordinator uses the container's configuration and filesystem.

## Load the relevant reference

Use `use_skill(skill_id="tomo", file="references/<file>")` in Tomo, or read the
linked file when operating from a repository checkout. Load only the topic and
support files needed for the task.

| Task | Reference |
| --- | --- |
| Command syntax, resource/action support, JSON input, schema discovery, reload behavior | [CLI configuration](references/configuration-cli.md) |
| Persistent agents, personas, model profiles, reasoning effort, workplace scopes | [Agents and models](references/agents-models.md) |
| Skill packages, per-agent tools/skills, reusable procedures, live plugins | [Skills and plugins](references/skills-plugins.md) |
| Future/recurring jobs, pause/resume, run history, background-work diagnosis | [Schedules](references/schedules.md) |
| Telegram, transcription, settings, approval defaults, limits, learning controls | [Channels and settings](references/channels-settings.md) |
| Full memory lifecycle: storage/scope, capture, wikilinks/aliases, recall, correction/move/forget, learning/consolidation, backup/restore | [Memory lifecycle and knowledge](references/memory-knowledge.md) |
| Accounts/passwords, API keys, sessions/history, approvals, saved/shared artifacts | [Accounts and sessions](references/accounts-sessions.md) |
| Update Tomo, control its service, manage skill packages, or uninstall | [CLI lifecycle](references/cli.md) |
| Install, pair, repair, or use a connector; configure/discover MCP | [Connections](references/connector.md) |
| Run tools on a specific tunnel/SSH host, `workplace=` arguments, wrong-host results | [Workplace targeting](references/workplaces.md) |
| Resolve config roots, environment, encryption, personas, or server bind | [Config and secrets](references/config.md) |
| Locate managed/source/container installs and connector binaries | [Install paths](references/install.md) |
| Choose a peer or plan and run workers | [Swarm](references/swarm.md) |

## Common requests: choose the complete workflow

| User intent | Do and verify |
| --- | --- |
| “Create a coding/research/ops agent” | Discover existing agents; create/update a persistent specialist, configure a usable profile, workplace, tools/skills, and read back its capabilities. Creating its record does not execute it. |
| “Use this model/provider” | Inspect/create the actual LLM profile, set default or the intended agent's profile ID, preserve other agents, and verify a bounded request when execution is asked for. |
| “Enable this tool/skill/MCP service” | Check installation/catalog/global state and per-agent selection; preserve the complete existing selection; discover exact runtime IDs and verify the capability. |
| “Run this every day/remind me later” | Establish agent, timezone, recurrence, and a self-contained job prompt; reuse the job ID, read its next run, apply runtime scheduling if needed, and inspect runs when execution is in scope. |
| “Provide private inputs / env keys / credentials without sharing their values with the agent” | Load `use_skill(skill_id="secret-store")`; request the task's own field schema with `tomo secret request`. Use `tomo secret apply` for backend-written env/JSON/secret files without reading their contents; choose the correct dialect. No connection/protocol is required. For HTTP consumption, also load `secure-http`; never use plaintext getters or paste values into chat. |
| “Connect Telegram/change voice handling” | Inspect supported channel settings, keep the intended allowlist, store credentials privately, apply connection changes, and verify in the intended chat without unsolicited messaging. |
| “Remember/link/correct/forget what you know” | Retrieve relevant memory; choose the store and scope; use entity wikilinks, exact supersession, or the supported correction/removal path; verify live facts, related pages, and any retained history. |
| “Change account/password/API access” | Inspect the actual account/key IDs, use supported account fields and protected input, preserve remaining account access, and verify the requested change. |
| “Delegate/use a swarm” | Discover capable enabled peers/templates; use one bounded handoff or an actual worker plan with ownership/dependencies, then synthesize verified evidence. |
| “Give me a report/export” | Complete the work on the actual workplace, register the deliverable in the current session, verify its artifact location, and provide the result without publishing it implicitly. |
| “Install/update/repair Tomo” | Identify managed/source/container deployment and live data roots, use its actual lifecycle, preserve data/secrets, and verify service plus the affected function. |

These are workflows, not a mandate to create every resource or run tests for a
configuration-only request. Use existing authorization, discovered state, and
the relevant reference to resolve routine choices; ask only for a material
missing destination, scope, credential, time, or preference.

## Capability and identity boundaries

- A configured agent is persistent; a swarm worker can be session-local. A
  configured agent ID, display name, profile ID, provider model name, workplace
  ID, job ID, and user/session ID are not interchangeable.
- Installing a skill, globally enabling it, assigning it to an agent, and enabling
  actual tools are separate operations. A skill is guidance, not execution access.
- Profile/agent/global configuration can differ from current session overrides.
  Read the affected scope rather than assuming one save changes all chats.
- Future scheduler sessions do not inherit this conversation. Job prompts must
  include their actual target, inputs, boundaries, output, and completion condition.
- Durable facts, user preferences, searchable knowledge, reusable procedures,
  episodic experiences, chat history, and deliverables belong in different stores.
- Secrets use protected input and public/masked output. CLI configuration does
  not require HTTP auth; connector tokens are not account API keys.

## Act on observed state

Resolve names to returned IDs, inspect before updating, and reuse records during
repair. For unfamiliar typed fields, use `tomo config <resource> schema --json`;
for special resources, use their documented input shapes. Use `--json` for
results and `--data -` or a protected JSON file for secrets/structured payloads.
Preserve fields outside the requested change; tool and skill assignment saves
replace the selection, so include the complete intended set.

Keep configuration and runtime operations distinct. A CLI save persists data;
it does not refresh active sockets, scheduler jobs, or channel connections.
Apply runtime changes through the installation's service/container lifecycle
when needed and verify the affected function. Do not invent `tomo chat`, CLI
OAuth login, MCP refresh commands, or missing agent tools.

Finish with the actual resource/host, change made, evidence, and remaining
blocker. A saved record, pairing code, installed binary, or masked credential
is not proof of an online connection or a successful remote task. Do not repeat
secret values or connector/account tokens in the final response.
