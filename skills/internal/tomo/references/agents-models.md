# Agents, personas, models, and access

Use this for a persistent specialist, agent configuration, model assignment,
workplace scope, or a request to change how an agent behaves. For temporary
workers on one task, use [swarm](swarm.md); do not create persistent agents just
to run an independent review.

## Discover the intended agent

On the coordinator, run `tomo config agents list --json` and
`show <agent-id> --json`. At runtime, `agent_info(action="list")` and
`agent_info(action="get", agent="<actual-id>")` expose roster/capabilities when
that tool is enabled. A display name, configured agent ID, session worker ID,
provider model name, and LLM profile ID are different identifiers.

Inspect the assigned profile, enabled tools, linked skills, and workplace scope
before choosing an agent. A role label alone does not grant expertise or access.
Use the smallest change that achieves the user's request.

## Create or update a persistent specialist

```bash
tomo config agents schema --json
tomo config agents create --set name=Builder --set role=coding --set description='Build and verify project changes' --json
tomo config agents update <returned-id> --set model_id=<profile-id> --set workplace_id=<workplace-id> --json
```

Replace angle-bracket placeholders with discovered IDs before execution. Create
accepts a `system_prompt` through `--data`; update does not accept that field.
The creation prompt seeds `$TOMO_HOME/agents/<id>/SYSTEM.md`. To change an existing
agent's prompt, inspect and edit that actual file within the requested scope;
read [config roots](config.md) before changing personas. Global `SOUL.md` affects
more than one agent; agent `SYSTEM.md`/`SOUL.md` customize that agent. Do not edit
shipped `defaults/` expecting it to replace existing live personas.

Creation is not an upsert. Save the returned ID and reuse it on subsequent edits.
Read back configuration and inspect the enabled roster. Disable with
`agents update <id> --set enabled=false` when the request is to stop selecting
an agent; deletion is a different action with data/link consequences. Configured
agents and session-local swarm workers have separate lifecycle/identity.

## Choose workplace access

| Intent | Fields |
| --- | --- |
| One workplace | `workplace_id` with `workplace_scope=single` |
| Selected workplaces | `workplace_ids` array; normalization uses list scope for multiple IDs |
| All registered workplaces | `workplace_scope=all`, without stale primary/list assignments |
| All tunnel workplaces | `workplace_scope=all_tunnels`, without stale primary/list assignments |

Inspect the saved result: normalization can derive scope/primary from the ID
list. To explicitly change to a broad scope, clear the existing primary/list in
the same update rather than leaving conflicting fields. Do not broaden access
merely to make a failing tool succeed. An assigned workplace is not necessarily
online, and a persistent assignment does not prove the current session/call is
using that workplace. Verify the actual execution host/path with runtime tools.

## Configure an LLM profile

```bash
tomo config llm-profiles list --json
tomo config llm-profiles schema --json
tomo config llm-profiles create --data @/absolute/profile.json --json
tomo config llm-profiles default <profile-id> --json
```

A profile holds `name`, `base_url`, provider `model`, encrypted `api_key`, enabled
state, and supported `reasoning_efforts`. Use the provider's actual values;
copying a model label does not establish availability. Default selection is
installation-wide; assigning `agents ... --set model_id=<profile-id>` targets
that agent. Resolution prefers its enabled profile, then the enabled default,
then an enabled fallback; inspect the actual choice when troubleshooting.

Blank/omitted API keys preserve the existing secret. Masked output proves only
that a value is set. Pass rotation data through a protected file/stdin, read back
the public flags, and perform a small real request through the intended agent
before claiming the model works. Check endpoint, model, authentication, timeout,
and `needs_reauth` for subscription profiles on failures.

Device/subscription login and session reasoning-effort selection are implemented
web/runtime workflows; the CLI does not expose OAuth or session setters. Profile
`reasoning_efforts` lists supported values; it is not itself a request to switch
one chat's selection. Use actual supported values and the current session's
available control. Do not fabricate a new provider login command.

## Tools, skills, and completion

Read [skills and modules](skills-modules.md) before replacing assignments.
`artifacts_enabled` controls that agent's artifact capability; it does not move
existing files. A newly created agent still needs usable model credentials,
relevant tools, and reachable workplaces.

Verify the agent's saved fields, effective capabilities, and one bounded task
when execution is part of the request. Report the agent/profile/workplace IDs
and observed result; never report configuration alone as a successful task run.
