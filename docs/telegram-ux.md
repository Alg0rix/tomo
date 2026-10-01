# Telegram interaction UX

Tomo keeps one quiet activity card per task. It moves through Thinking, Working,
Writing, and Waiting for approval / your answer; displays running tools, the last
three results, plan progress, elapsed time, and approval mode. A Stop button stays
available during the task. Private reasoning and raw tool output stay out of the
Telegram progress card.

Each model round streams into one quiet editable preview. Tool commentary turns
that preview into a single progress message, then the next round starts fresh.
The completed answer is sent as a new message below progress and approval cards;
its temporary preview is removed only after successful delivery. Overflow arrives
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

## Channel context and file delivery

Telegram turns explicitly tell the model that the user is reading Telegram, not
the web UI. This is a static block in the first system message, before history,
not a suffix after the conversation. It contains no chat IDs, tokens, filenames,
or timestamps; the channel's tool schemas and provider cache routing key stay
stable between model rounds and successive turns. Web-only `render_ui` is hidden;
Files-panel instructions and HTML image embeds are replaced with channel-specific
delivery guidance.

Use `save_artifact` to store a deliverable, then `telegram_send_file` with its
`filename` to upload it to the current chat and topic. Saving alone is not sending.
The tool accepts only current-session artifacts, not arbitrary paths, URLs, chat
IDs, or another session's files. It rechecks chat access before uploading, keeps
stored files on failure, and reports success only after Telegram confirms delivery.

`kind=auto` sends PNG/JPEG images up to 10 MB via `sendPhoto`; other files are
uploaded via `sendDocument` (50 MB limit). Choose `kind=document` to preserve an
image without compression. Captions are plain text, up to 1024 characters. The
tool is unavailable in web turns and uses the existing bot transport—no token,
shell helper, or public tunnel needs to be given to the model.

## Commands

| Command | Behavior |
| --- | --- |
| `/id` | Show the current chat ID without running the model |
| `/help`, `/start` | Show guidance |
| `/new` | Fresh history, retained approval mode; stop active work first |
| `/stop` | Cancel the current turn, clear waiting inputs, and wake pending waiters |
| `/status` | Show activity, approval mode, input mode, and queue depth |
| `/steer <text>` | Inject guidance into the current run at its next model round |
| `/queue <text>` | Run a separate turn after the current one, in FIFO order |
| `/queue list`, `/queue clear` | Inspect or cancel waiting tasks; clear keeps the current run |
| `/interrupt <text>` | Stop current work, clear waiting inputs, then run the replacement |
| `/mode steer\|queue\|interrupt` | Choose how ordinary messages behave while busy |
| `/manual`, `/smart` | Change approval mode for this conversation |
| `/auto` | Toggle automatic approvals; existing hardline restrictions remain |

The activity card also provides Steer / Queue / Interrupt buttons, bound to the
initiating person and topic. Ordinary messages default to **steer**. An explicit
command overrides the mode for that message. Modes last for the current bot
process; `/new` keeps the selected mode. Idle commands run their payload as an
ordinary turn, even when the payload starts with another slash command.

Steer feedback starts as “received” and changes only when the loop emits the
correlated steer receipt: the agent has read it into the next model round. This
confirms consumption, not that the requested change has already succeeded. If
guidance arrives during startup, after the final drain, while receiving media,
or during an approval wait, it becomes a separate follow-up instead of being
lost. Unread guidance is bounded to 20 messages; explicit waiting tasks to 10.
Queued messages receive position, starting, and completed/failed feedback.
Interrupt waits for the previous turn's cleanup before starting its replacement.
Stop and interrupt clear waiting tasks and unread guidance; dispatched tool
effects cannot be undone by cancellation.

Pending questions take precedence over ordinary text steering; explicit commands
remain controls. Media sent during busy work queues as a separate turn, or
replaces work in interrupt mode. Waiting albums preserve their files and original
reply/topic context. Only the initiating person in the same topic can inject or
change busy work. Other chats continue independently; the dispatcher limits
concurrent chats to 16. Queue and receipt state are in memory and do not survive
a bot restart; shutdown cancels the waiting tasks.

Public assistant commentary accompanying tool calls appears as quiet progress
messages (at most 10 per turn). Subagent completion/failure updates the activity
card. Private reasoning and raw tool results remain excluded from Telegram.

## Comparison with the local Hermes checkout

The references inspected include `tmp/hermes-agent/gateway/run_busy.py` and
`run_inbound.py` for steer, FIFO queue, busy modes, and feedback, plus
`tmp/hermes-agent/plugins/platforms/telegram/adapter.py`,
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

## Voice and media

Send photos, documents, voice notes, audio, video, round video notes, animations,
or stickers. Files are downloaded only for approved chats and saved as ordinary
session attachments, visible in Tomo's Chat history. Photos choose the largest
available size and reach vision-capable models through the existing image input
path. Text and supported documents use Tomo's existing attachment extraction.
Video and animated stickers are saved as files; this does not add video frame
analysis. Both announced and streamed download sizes are bounded to 20 MiB.
Telegram's hosted API also has a [20 MB download limit](https://core.telegram.org/bots/api#getfile).

Albums arriving within a fixed 800 ms collection window share one turn, up to
10 files. Rich-message media blocks use the same ingestion path, up to 10 files.
An album arriving late is treated as another inbound attachment and queues if
a turn is already running. Captions and speech are user content,
so a caption like `/new` does not execute a bot command. Media received during
an existing task becomes a separate queued turn instead of being silently discarded or
used as an approval answer. Stop cancels collection, download, and transcription;
other chats and callbacks remain responsive during those operations.

Enable **System → Channels → Voice transcription**, enter the service base URL,
model, and API key, then save. Tomo posts multipart audio to
`<base URL>/audio/transcriptions` using the
[OpenAI-compatible transcription shape](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create).
This can use an external provider or your local compatible service. Audio goes
to that configured service only when transcription is enabled. The key is
encrypted and masked using the same settings protection as the bot token.
ChatGPT subscription credentials are not automatically used as transcription
credentials. The original audio remains attached and the transcript becomes
the user message. With transcription disabled, failed, or empty, the file stays
saved and the bot explains how to continue; without a caption it does not run
an agent on unrecognized speech.

## Native rich answers

Enable **System → Channels → Rich answers** to use Telegram's
[rich-message API](https://core.telegram.org/bots/api#sendrichmessage).
Native headings, tables, lists, code, and fenced `math`/`latex` blocks keep their
structure. Raw HTML is escaped, and Markdown images do not trigger remote
media fetches or inject control buttons. Both private and group chats stream
into one editable rich preview, using the same progress and final-message ordering
as legacy formatting. Received rich text and rich media blocks are also accepted.

Definite rich API rejections fall back to the existing balanced HTML formatting,
including long-answer chunking. Unsupported rich endpoints are remembered for
the lifetime of the current API client. Timeouts, permission failures, and ambiguous server errors never
trigger a second legacy send. Approval and stop buttons retain their existing
message/sender/session checks. Rich answers are opt-in because Telegram client
support and copying behavior vary.

An in-progress turn does not survive a process restart. Stop cancels the agent
turn and pending approvals; synchronous
or remote tools already dispatched may still finish, and completed effects are
not undone. Persisted update cursors prevent replay of admitted updates,
and stale buttons return an expiry notice. A crash after admission can interrupt
a turn; its received history remains available when already persisted. Telegram
forum threads have delivery context and bound controls but still share the
chat's conversation history. Live delivery to a real bot requires a smoke test
after restarting Tomo.
