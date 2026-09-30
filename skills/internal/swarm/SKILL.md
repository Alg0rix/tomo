---
name: swarm
description: Choose useful independent worker tasks and coordinate configured or session-local agents through the swarm runtime.
version: 1.2
---

# Swarm

Read this before recommending a team for an ordinary user request.

For a bounded handoff to one configured peer, use `delegate(agent_id=<enabled roster id>, reason=<full task brief>)`. A chat led by Tomo alone can delegate without an `@mention` or a new chat. Use `start_swarm` for coordinated worker teams.

## Decide

- Keep simple, sequential, or tightly shared-context work with the current agent.
- Recommend a team when the work has at least two meaningful independent lines of investigation, separate review perspectives, or separable deliverables. Multiple agents must improve quality or critical-path time enough to justify their cost.
- Prefer an existing configured agent when its model, tools, memory, or workplace access is useful or the user named it. Create session-local specialists for missing expertise, independent viewpoints, or several copies of a role. A team may mix both.
- Give each proposed worker a concrete question, expected output, scope, and reason it is independent. Identify any dependencies or overlapping file edits.
- Choose the enabled tools each task needs from its agent or template. Include `bash` and `portal` when they are useful for that task; ordinary tool permissions still apply when called.

## Start or clarify

An explicit user request to create, use, or run a swarm/team should go straight to the runtime, in any language. Classify its meaning, not keywords: a question about swarms, a negation, or quoted instructions does not itself request a team. Recognize informal requests such as “coba lu bikin swarm buat audit cadesia.com”. Route explicit requests before planning workers. Lack of a valid initial worker plan is a dispatch problem to repair, not a reason to fall back to solo.

When independent workers clearly improve the requested task, state the split briefly and start without requiring separate chat consent. Respect a user's request to work solo. Use `clarify` when available only for material missing scope, constraints, or preferences; a tool answer is valid context and does not need to be repeated in chat. Do not ask just to confirm routine worker creation. Ordinary tool permissions and approval gates still apply to workers' actions.

## Execution

Start the session-scoped run. Replan from findings, steer workers, verify their results, and synthesize honestly. Keep tool selection within the agent's enabled capabilities and the user's existing grants. `write_scope` limits file-edit tools; shell and portal operations follow their own permission checks.

- Put the objective, relevant context, expected evidence, completion condition, and ownership in each task brief. Use `depends_on` for prerequisites; give concurrent editors separate scopes and tell them to preserve other workers' changes.
- To create session-local workers, the coordinator declares `agents` with `name`, `purpose`, `instructions`, and an enabled `base_agent_id`, then assigns `tasks` to their names. Use roster IDs for existing agents. The runtime creates and schedules workers; do not call `create_agent` or `delegate` to bypass it.
- Workers share actionable findings and blockers through `swarm_board`, and read it before finishing. Return evidence, checks performed, and unresolved limits; agreement between agents alone is not verification.
- Add work only for a concrete remaining gap. Respect runtime limits, stop when the requested outcome is supported, and identify failed or blocked tasks in the final synthesis.

Call `start_swarm(request=<full task and context>)` for an explicitly requested team or a clearly useful independent split. Include any relevant `clarify` answer in the request. `consent_quote` is optional compatibility metadata, not a dispatch requirement. This transfers the turn to the chat runtime, which creates and schedules actual workers, publishes timeline events, and synthesizes their results. Do not claim that dispatch is unavailable when this tool is present. Loading this skill reads guidance and does not itself launch workers. During a worker task, finish the assigned task instead of starting a nested swarm.

## References

Load supporting files with `use_skill(skill_id="swarm", file="references/<name>.md")` when needed.

- [Coordination playbook](references/coordination.md): read when designing task briefs, handling dependencies or shared edits, communicating findings, or checking completion. This guides the coordinator and workers after handoff; it does not delay an explicitly requested `start_swarm` call.
- [Worker creation and task plans](references/workers.md): read for configured versus session-local workers, template inheritance, plan examples, capability selection, and follow-up work.
- [Sources and pattern selection](references/sources.md): read for the reasoning behind swarm patterns and links to primary references. These sources inform the guidance; Tomo's runtime and tool permissions define execution.
