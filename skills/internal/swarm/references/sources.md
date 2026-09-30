# Swarm sources and pattern selection

Primary sources checked on 2026-09-30. The recommendations below are adapted to Tomo; external libraries are not required to use this skill.

## Architecture patterns

[Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) distinguishes parallel work on independent subtasks, dynamic orchestrator–worker decomposition, and evaluator feedback loops. Select a pattern according to dependencies and measurable benefit; extra agents trade cost and latency for potential quality. The article is foundational, and notes that its tooling has evolved since publication.

## Delegation and coordination

[Anthropic: How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) describes scoped delegation, relevant context, source-backed outputs, and effort matched to complexity. Broad independent research suits parallel workers; tasks needing tightly shared context are harder to coordinate. Its benchmark and token-use results describe that research system, not guaranteed Tomo performance.

## Manager versus handoff

[OpenAI Agents SDK: Agent orchestration](https://openai.github.io/openai-agents-python/multi_agent/) distinguishes a manager retaining control and combining specialist outputs from a handoff where a specialist becomes the active agent. It also distinguishes model-directed planning from code-defined flow. Tomo's coordinator plans tasks while its scheduler enforces dependencies and concurrency; workers return results for coordinator synthesis. `start_swarm` transfers the chat turn to this runtime, rather than implementing the SDK API.

## Historical Swarm

[OpenAI Swarm repository](https://github.com/openai/swarm) illustrates lightweight agents and handoffs. Its README identifies it as experimental and educational, and says Agents SDK has replaced it for production use. Use it to understand the original concepts; use maintained SDK documentation for current OpenAI implementation details. Tomo's skill name does not imply a dependency on the OpenAI Swarm package.
