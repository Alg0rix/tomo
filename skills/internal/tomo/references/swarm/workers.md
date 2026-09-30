# Create and assign swarm workers

## Enter the runtime

Create the worker plan in the current main chat, using its selected model, context, and reasoning settings. Read the enabled worker/template catalog in the current system prompt; use `agent_info` when available for further capabilities. A request like “Test swarm lagi” can use a small, tool-free plan to check real worker execution.

Call `start_swarm(request=<complete task>, plan=<worker plan below>)`. The runtime validates and executes this plan; there is no separate planning model call. Validation errors return to the current model so it can correct the actual rejected fields. Results return as a tool result for verification and synthesis in the same main chat loop.

No separate chat consent or quote is required; include relevant answers obtained through `clarify`. A question about swarms does not itself request execution.

## Choose existing or temporary workers

- **Configured worker:** assign its enabled roster ID directly in `tasks[].agent_id`; leave `agents` empty if no temporary workers are needed. Prefer it when the user selected it or its existing capabilities fit.
- **Session-local worker:** declare it in `agents`, then refer to its name in the same plan's tasks. The runtime generates its ID and persists it in the chat session rather than creating a permanent configured agent.
- **Reuse:** subsequent plans can assign an existing session worker using its roster ID. Do not recreate a role simply to give it another task.

For each new worker, supply `name`, `purpose`, `instructions`, and `base_agent_id`. Use distinct names. The base must be an enabled configured agent; it defaults to the coordinator if omitted. Its model, base prompt, workplace, and available tools provide the template for execution. Role instructions specialize the worker; they do not grant capabilities or replace task boundaries. The coordinator can be a template but cannot itself be assigned a worker task.

## Example plan for the start_swarm tool

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
  ]
}
```

Provide the actual research and criteria in the request/context; placeholders alone are not sufficient inputs. Both tasks can run concurrently. The coordinator synthesizes their results without needing a separate synthesis worker.

## Tools, editing, and prerequisites

Select tool names from the configured agent or base template's enabled catalog. Specify `tools` explicitly: `[]` means no selected work tools, while omission currently selects all eligible enabled tools. Every worker receives `swarm_board` automatically. Workers cannot use `create_agent`, `delegate`, or `start_swarm` to spawn more workers.

For an editor, assign relative file or directory paths in `write_scope`, the required enabled editing tools, and a brief with ownership boundaries. For shell edits, also state the boundary in the brief; `write_scope` enforces file-edit tools only. Give an integration/check task `depends_on` containing the prerequisite task keys. Keys must be unique, prerequisites must exist, and the graph must have no cycles.

## Add follow-up work in the main chat

Read the returned worker results, including task status, worker IDs, and evidence. For a specific remaining gap, call `start_swarm` again with a fresh task plan and relevant findings. Reuse existing session worker IDs when appropriate. Dependencies apply within each submitted run; include earlier results directly in a follow-up brief instead of referencing a task key from another run. Workers share findings through the [board](coordination.md) while running. Failed prerequisites block dependent tasks; report or resolve failures using evidence.

The authoritative implementation is `app/runtime/coordinator/swarm.py`; session worker records are in `app/models/mixins/swarm.py`. These examples describe that runtime, not OpenAI SDK worker-creation calls.
