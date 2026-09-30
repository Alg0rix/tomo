# Telegram interaction UX

Tomo keeps one quiet activity card per task. It moves through Thinking, Working,
Writing, and Waiting for approval / your answer; displays running tools, the last
three results, plan progress, elapsed time, and approval mode. A Stop button stays
available during the task. Private reasoning and raw tool output stay out of the
Telegram progress card.

The first answer preview becomes the completed answer in place. Overflow arrives
in additional messages with notifications disabled. Markdown supports headings,
emphasis, lists, links, quotes, inline code, and fenced code; tables become labeled
rows for mobile screens. Each long-answer chunk preserves Unicode, escaped
entities, and balanced tags. Unsupported nesting is flattened; rejected HTML gets
a plain-text fallback. Rendering follows the [Telegram formatting restrictions](https://core.telegram.org/bots/api#formatting-options).

Approval cards show the tool, argument preview, findings, and the choices allowed
by the existing permission gate. Environment values and common secret fields are
redacted. Allow once, This session, Always allow, and Deny have their normal Tomo
semantics. Always allow requires a separate confirmation; repeated first taps
cannot grant a persistent permission. A control is bound to its session, chat,
thread, prompt message, and the person who started the task. Revoked chats,
forwarded controls, other group members, unavailable choices, and expired requests
cannot approve it. Web resolvers also check session ownership.

Clarification cards accept a choice or a reply to the question. Type an answer
also enables unquoted text capture in a private chat when exactly one question is
waiting. Group answers must reply to the question and come from the initiating
person. If a prompt cannot be delivered, the approval is denied / clarification
returns empty rather than waiting invisibly. Resolved and expired controls lose
their buttons, including when resolved through the web UI.

## Commands

| Command | Behavior |
| --- | --- |
| `/id` | Show the current chat ID without running the model |
| `/help`, `/start` | Show guidance |
| `/new` | Fresh history, retained approval mode; stop active work first |
| `/stop` | Cancel the current turn and wake pending waiters |
| `/status` | Show activity and approval mode |
| `/manual`, `/smart` | Change approval mode for this conversation |
| `/auto` | Toggle automatic approvals; existing hardline restrictions remain |

During a task, extra text from its initiating person becomes guidance through
Tomo's existing steer inbox. It is not silently dropped or run as another
concurrent turn. Pending questions take precedence over steering. Other chats
continue independently; the dispatcher limits concurrent chats to 16.

## Comparison with the local Hermes checkout

The reference inspected is `tmp/hermes-agent/plugins/platforms/telegram/adapter.py`,
plus its approval, clarify, status, formatting, and typing regression tests. This
is a comparison of the requested interaction flow, not a claim of complete
Hermes feature parity.

| Interaction | Local Hermes reference | Tomo implementation |
| --- | --- | --- |
| Typing | Periodic action with cooldown after failures | Periodic action, pause during HITL, cooldown; failure does not stop the turn |
| Progress | Editable status messages and tool feedback | One activity card includes tools, plan progress, elapsed time, approval mode, and Stop |
| Formatting | Markdown conversions with protected code and table conversion | CommonMark tokens rendered to supported HTML; independent balanced Unicode-safe chunks |
| Permanent approval | Always choice resolves the approval | Always opens a separate confirmation bound to the same request and actor |
| Clarification | Choice buttons and typed Other | Choice buttons, explicit reply capture, actor/thread binding, stale-request checks |
| Web history | Separate gateway session handling | Same turn manager and history used by Chat; admin can watch and resolve there |

## Validation and limits

The tests drive the real turn manager, permission gate, clarification waiters,
and SQLite history with a scripted model and mocked Telegram transport. They
cover allow/deny, cancellation, concurrent chat responsiveness, actual polling
while HITL waits, spoofed callbacks, stale controls, prompt delivery failure,
web ownership, long formatted replies, flood retry, and typing failure.

```bash
uv run pytest -n 0 tests/unit/channels/test_telegram*.py \
  tests/integration/test_telegram_approval_api.py
```

This adapter remains text focused. Telegram voice/media ingestion and the newer
rich-message API are not part of this change. An in-progress turn does not survive
a process restart. Stop cancels the agent turn and pending approvals; synchronous
or remote tools already dispatched may still finish, and completed effects are
not undone. Persisted update cursors prevent replay of admitted updates,
and stale buttons return an expiry notice. A crash after admission can interrupt
a turn; its received history remains available when already persisted. Telegram
forum threads have delivery context and bound controls but still share the
chat's conversation history. Live delivery to a real bot requires a smoke test
after restarting Tomo.
