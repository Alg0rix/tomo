# Create and assign swarm workers

## Enter the runtime

In the root chat turn, an explicitly requested or clearly useful swarm goes through `start_swarm`. For example, with the current user message `Bikin swarm buat review hasil riset ini`, call:

```text
start_swarm(
  request="Review the supplied research with independent evidence and coverage checks. Include the supplied material and relevant prior context here."
)
```

Use the actual task and relevant context. No separate chat consent or quote is required; include answers obtained through `clarify` if material scope was missing. A question about swarms does not itself request worker execution. The tool transfers the turn; the coordinator creates the worker plan below. This JSON is coordinator output, not an argument to `start_swarm`, a tool named `create_worker`, or a command the chat agent executes directly.

## Choose existing or temporary workers

- **Configured worker:** assign its enabled roster ID directly in `tasks[].agent_id`; leave `agents` empty if no temporary workers are needed. Prefer it when the user selected it or its existing capabilities fit.
- **Session-local worker:** declare it in `agents`, then refer to its name in the same plan's tasks. The runtime generates its ID and persists it in the chat session rather than creating a permanent configured agent.
- **Reuse:** subsequent plans can assign an existing session worker using its roster ID. Do not recreate a role simply to give it another task.

For each new worker, supply `name`, `purpose`, `instructions`, and `base_agent_id`. Use distinct names. The base must be an enabled configured agent; it defaults to the coordinator if omitted. Its model, base prompt, workplace, and available tools provide the template for execution. Role instructions specialize the worker; they do not grant capabilities or replace task boundaries. The coordinator can be a template but cannot itself be assigned a worker task.

## Example coordinator plan

Assume `main` is the actual enabled coordinator/template ID in this example. Replace it with a real roster ID in use. These workers inspect supplied material without tool calls, so `tools` and `write_scope` are empty.

```json
{
  "agents": [
    {
      "name": "Evidence reviewer",
      "purpose": "Check whether supplied evidence supports the claims",
      "instructions": "Review only the supplied material. Cite claim and source locations. Distinguish missing evidence from a disproved claim; do not imply external verification.",
      "base_agent_id": "main"
    },
    {
      "name": "Coverage reviewer",
      "purpose": "Check coverage against the user's research question",
      "instructions": "Compare the supplied research with the stated question. Identify material omissions and assumptions, without repeating the evidence review.",
      "base_agent_id": "main"
    }
  ],
  "tasks": [
    {
      "key": "evidence-check",
      "agent_id": "Evidence reviewer",
      "brief": "Check claims against the supplied research and excerpts. Return claim locations, supporting or conflicting evidence, and unresolved verification needs. Finish after covering each material claim.",
      "depends_on": [],
      "write_scope": [],
      "tools": []
    },
    {
      "key": "coverage-check",
      "agent_id": "Coverage reviewer",
      "brief": "Check whether the supplied research answers the original question. Return gaps ranked by impact, with locations and focused follow-up questions. Finish after checking each requested criterion.",
      "depends_on": [],
      "write_scope": [],
      "tools": []
    }
  ],
  "messages": []
}
```

Provide the actual research and criteria in the request/context; placeholders alone are not sufficient inputs. Both tasks can run concurrently. The coordinator synthesizes their results without needing a separate synthesis worker.

## Tools, editing, and prerequisites

Select tool names from the configured agent or base template's enabled catalog. Specify `tools` explicitly: `[]` means no selected work tools, while omission currently selects all eligible enabled tools. Every worker receives `swarm_board` automatically. Workers cannot use `create_agent`, `delegate`, or `start_swarm` to spawn more workers.

For an editor, assign relative file or directory paths in `write_scope`, the required enabled editing tools, and a brief with ownership boundaries. For shell edits, also state the boundary in the brief; `write_scope` enforces file-edit tools only. Give an integration/check task `depends_on` containing the prerequisite task keys. Keys must be unique, prerequisites must exist, and the graph must have no cycles.

## Steer and add follow-up work

To steer a running worker, the coordinator returns `messages` entries with its actual `to_agent_id` and a concise `content`. Workers communicate using the [board](coordination.md). Names in a new plan are resolved to IDs by the runtime; use actual IDs for messages.

Add a task with a fresh key for a specific remaining gap, reusing an appropriate worker. Completed dependency results are passed to dependent workers. A failed prerequisite blocks dependent tasks, so resolve or replan the missing work rather than assuming its result exists. Finish with supported findings and disclose remaining failures.

The authoritative implementation is `app/runtime/coordinator/swarm.py`; session worker records are in `app/models/mixins/swarm.py`. These examples describe that runtime, not OpenAI SDK worker-creation calls.
