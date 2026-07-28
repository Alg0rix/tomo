"""Prompt templates for ATG LLM calls (compile, refine, node binding, repair).

All templates in one place so prompt tuning never touches control-flow code.
Adapted from the ATG paper's prompt design for Tomo's string-tool runtime.
"""

GRAPH_JSON_SCHEMA = """\
Output ONLY a single fenced ```json block with this exact shape:
{
  "nodes": [
    {
      "id": "n1",
      "goal": "one-sentence intent of this step",
      "tool": "read_file",
      "args_template": {"path": "src/config.py"},
      "inputs": [{"name": "source", "from_node": "n1", "key": "result"}],
      "outputs": ["result"],
      "deps": ["n1"]
    }
  ]
}
Rules:
- The graph must be a DAG (no cycles). "deps" lists node ids that must finish first.
- An ATOMIC node performs exactly ONE tool call: set "tool" to a tool name and
  fill "args_template" with its arguments.
- If a step is too coarse for a single tool call, set "tool": null (a COMPOSITE
  node) — it will be refined into a subgraph later. Composite nodes still
  declare "outputs" (the interface they promise).
- Argument values may embed placeholders "${node_id.result}" that resolve to an
  upstream node's output string at execution time. Every placeholder and every
  "inputs" entry must reference a node listed in "deps" and one of its declared
  "outputs".
- Every node should declare "outputs": ["result"] (Tomo tools return a string).
- Keep goals concrete and self-contained; a worker will execute each node
  WITHOUT seeing the conversation."""

COMPILE_SYSTEM = """\
You are a task-graph compiler for an autonomous tool-using agent. You decompose
a user task into a directed acyclic graph (DAG) of atomic tool-use steps that
can be scheduled in dependency order, with independent branches running in
parallel.

Available tools (name(args) -> outputs [read-only|mutating]):
{catalog}

{schema}"""

COMPILE_COARSE_USER = """\
Compile the following task into a DAG of 3-7 nodes. Prefer atomic nodes
(single tool call); use composite nodes (tool: null) only for steps that
genuinely need several tool calls.

Task: {goal}
{context}"""

COMPILE_REFINE_USER = """\
Refine composite node "{node_id}" into a subgraph of 2-7 nodes.

Node goal: {goal}
Available upstream outputs (usable as deps/placeholders): {external_context}
REQUIRED interface — the subgraph as a whole MUST produce exactly these output
keys (each declared by exactly one child): {outputs}

Use child ids "1", "2", ... (they will be namespaced as {node_id}.<n>).
"deps" entries may reference sibling child ids or the external node ids listed
above. Do not reference any other nodes.

{schema}"""

COMPILE_RETRY_SUFFIX = """

Your previous output was invalid:
{errors}

Fix these problems and output the corrected ```json block."""

NODE_BIND_SYSTEM = """\
You execute ONE step of a task graph by choosing a single tool call.
You do not see the conversation — only this step's goal and the outputs of the
steps it depends on. Output ONLY a fenced ```json block:
{{"tool": "<tool_name>", "args": {{...}}}}

Available tools:
{catalog}"""

NODE_BIND_USER = """\
Step goal: {goal}
{tool_constraint}
Argument template (may contain unresolved values): {args_template}

Upstream results:
{upstream}

Produce the single concrete tool call for this step."""

NODE_BIND_RETRY_SUFFIX = """

Your previous output was invalid: {errors}
Output the corrected ```json block."""

REPAIR_USER = """\
A subgraph of the task graph failed and must be recompiled.

Failed step: {failed_goal}
Tool attempted: {tool} with args {args}
Error: {error}

Produce a replacement subgraph of 1-5 nodes that achieves: {region_goal}
Do NOT repeat the failed approach — address the error.
Available upstream outputs (usable as deps/placeholders): {external}
REQUIRED outputs the subgraph must produce (interface to preserve): {outputs}
Use child ids "1".."5" (namespaced as {region_id}.<n>); deps may reference
the external node ids listed above.

{schema}"""
