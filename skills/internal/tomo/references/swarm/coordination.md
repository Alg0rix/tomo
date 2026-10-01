# Swarm coordination playbook

This is an application of the [primary references](sources.md) to Tomo, with local examples. It is guidance for task design, not another dispatch API.

## Choose a useful split

Split by independent question or deliverable, rather than assigning several agents the whole request. Examples:

| Work | Useful split | Integration |
| --- | --- | --- |
| Compare services | Investigate different services using the same criteria | Coordinator checks evidence and compares |
| Audit a website | Inspect performance and accessibility separately | Coordinator combines reproducible findings |
| Change multiple modules | Assign modules with agreed interfaces and separate ownership | Integration task depends on the changed modules |
| Review a change | Use distinct correctness and security questions | Coordinator reproduces important findings |

A chain where every step needs the previous result gains little from concurrent workers. Keep tightly coupled changes under one owner. Independent reviews can share read access, but repeated opinions need supporting evidence.

## Write a task brief

Use the fields the coordinator already accepts: `key`, `agent_id`, `brief`, `depends_on`, `write_scope`, and `tools`. Put task-specific details inside `brief`; do not invent additional API fields.

Include:

- Objective: a bounded question or deliverable linked to the user's outcome.
- Context: relevant decisions, inputs, interfaces, and dependency results; do not assume a new worker knows the full conversation.
- Boundary: assigned files or subject area, permitted actions, and other workers' ownership.
- Evidence and output: source URLs, file locations, reproducible observations, or artifacts needed by the coordinator.
- Completion: an observable acceptance condition and the point at which to return a blocker.

Example implementation brief:

```text
Objective: implement the agreed response formatting in app/services/report.py.
Context: use the contract returned by task contract-review.
Ownership: edit only app/services/report.py. Other workers are editing other
modules; preserve their changes. Report interface conflicts before proceeding.
Output: changed file, relevant check results, and unresolved limitations.
Done: the formatter follows the agreed contract and relevant checks pass.
```

In its task plan, set `depends_on=["contract-review"]`, `write_scope=["app/services/report.py"]`, and select actual enabled tools. Review-only tasks need no write scope. Choose configured agents for relevant access or expertise and session-local agents for gaps.

## Coordinate shared work

Tomo starts tasks after their prerequisites finish successfully and serializes overlapping file-edit scopes. Set scopes narrowly enough to allow useful concurrency. The scheduler currently permits four active workers and twelve tasks per run; these are ceilings, not targets. The implementation in `app/runtime/coordinator/swarm.py` is authoritative if limits change.

Shell and portal actions have separate permission checks: a file-edit scope does not isolate those operations. Include ownership boundaries in briefs even when using shell tools. Resolve shared interface decisions before concurrent edits and assign integration to one owner.

## Use the board deliberately

Inside an active worker task:

```text
swarm_board(action="publish", content="Finding: ... Evidence: ... Impact: ...")
swarm_board(action="send", to_agent_id="<actual worker id>", content="Blocker: ... Needed: ...")
swarm_board(action="ask", content="Which contract should I follow? Evidence: ...")
swarm_board(action="read")
```

Publish findings that change another task or the plan; send targeted messages to actual roster IDs. Use `ask` for a blocker or decision that needs the main coordinator: this pauses the worker for up to 120 seconds. The coordinator answers with `send`, the worker agent and task IDs, and `reply_to_event_id` matching the question event ID. On timeout, report the unresolved blocker honestly. Do not invent an answer or permission. Report unavailable tools or broken assumptions promptly. The board returns the latest thirty visible finding, message, or completion events and limits a post to 8,000 characters, so keep updates concise and put lasting evidence in task outputs or artifacts. Shared findings and targeted messages enter worker context automatically between model rounds, including a final-answer checkpoint. Delivery receipts prove context delivery, not action or agreement. Running tools and model requests are not interrupted. Use `to_task_id` to target one of several concurrent tasks owned by the same agent; messages to ended tasks are rejected.

The main coordinator reviews new shared findings, questions addressed to it, user steering, intermediate completions, and a 30-second progress heartbeat while workers are active. One review runs at a time, coalescing concurrent updates. It can answer questions, forward evidence, and correct live tasks through the board. Composer steering belongs to the coordinator; it translates guidance for the relevant workers. Coordinator review failures are visible and workers continue. Cancelling the run also cancels the review and any waiting workers.

Worker and coordinator updates append to existing model conversations. Repeated coordinator reviews retain the same message prefix and tool schema, with new board events at the end, to preserve prompt caching; actual hit rates depend on the model provider. Existing context compression still applies when conversations grow.

This shared board acts as the colony's working memory. Workers keep their own task context and ownership; the coordinator reconciles shared evidence and delivers relevant guidance. Posting an assertion does not make it verified knowledge. This does not create unlimited workers, automatically widen scopes, or resume side effects after a process restart.

## Verify and finish

Return findings or changes, supporting evidence, checks and outcomes, and open questions. Distinguish observations from inference. Use only the sources and tools actually accessed.

The coordinator checks results against the request and reconciles conflicts using evidence. For code, run appropriate checks on the integrated state; individual worker success does not establish compatibility. For research, check source support and recency for consequential claims.

Replan for a specific missing answer, failed prerequisite, or integration problem. Do not repeat an unchanged failed approach or create workers just to fill capacity. Report failed and blocked work honestly when synthesizing; runtime completion alone does not mean the requested outcome was achieved.


## Follow coordination progress

The web worker lane shows “Menunggu jawaban main” while a question is pending. It clears when the correlated reply enters worker context, not when the coordinator merely sends it. If waiting times out, the lane shows “Jawaban main belum diterima” while that task remains active. Persisted resolution events keep refresh and resumed views consistent; ended or cancelled runs do not retain waiting badges.

Telegram folds colony progress into its existing editable activity message: task counts, up to three active or queued worker states, and two recent board updates. It distinguishes waiting for the main coordinator from a human approval or clarification request. Main reviews, questions, replies, delivery, timeout, and review failures are visible without sending a separate message per board event. Existing Stop, Steer, Queue, Interrupt, and `/status` controls continue to apply. These UI updates do not trigger additional model requests or change the prompt-cache prefix.
