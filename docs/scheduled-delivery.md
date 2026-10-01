# Scheduled channel delivery

Jobs created with the `schedule` tool in Telegram automatically retain that chat
and topic. Updates keep the destination; Telegram tool calls can only list/manage
jobs in their own topic. The model cannot supply a recipient, bot token, or target
object. Web-created jobs remain local and existing jobs are not silently rerouted.

## Execution and delivery are separate

- Channel jobs use a session per job, not the shared web scheduler session. Their
  histories and artifacts cannot leak into another job's conversation.
- Channel capabilities and scheduled-run instructions are in the leading system
  message. Destination IDs and credentials do not enter the reusable prompt prefix.
- Telegram sends final text automatically. The agent uses `save_artifact` and
  `telegram_send_file` for files; merely saving a file does not deliver it.
- Current chat permission, enabled state, and bot identity are checked before the
  agent starts and before **every HTTP attempt**, including flood retries.
- A completed run and its pending final are committed together in SQLite.
  Delivery never re-runs the agent or repeats its file tools.

`action=runs` and the schedule-run API expose `delivery_status`, `delivery_error`,
and `delivery_receipt` separately from the execution status. A job can complete
successfully even when its delivery is blocked or unconfirmed.

| Delivery state | Meaning |
| --- | --- |
| `local` | No external destination |
| `not_attempted` | Channel execution failed before producing a deliverable final |
| `empty` | Completed with no final text |
| `pending` | Final saved, no sender has claimed it |
| `sending` | One sender claimed the final |
| `sent` | Channel returned a delivery receipt |
| `blocked` | Destination no longer authorized; final send not attempted |
| `unknown` | Send failed, was partial, or process stopped during delivery |

On startup, `sending` becomes `unknown`; pending finals are drained immediately
and every 30 seconds. Compare-and-set claims prevent competing drainers from
sending the same pending final. `unknown` is **not** automatically retried: Telegram
does not provide an idempotency key, so retrying might duplicate a delivered message.
A partially delivered long answer is also unconfirmed, not treated as a clean block.

This guarantees durable **completed finals**, not exactly-once execution of arbitrary
agent tools. Attachments are sent during execution and are not an attachment outbox.
Crash recovery assumes one scheduler-owning application process, as does the
in-process APScheduler engine; starting multiple engines on one database is not a
supported deployment model.

## Adding another channel

Implement `DeliveryChannel` in [app/channels/delivery.py](../app/channels/delivery.py)
and register it during application/channel initialization:

```python
from app.channels.delivery import register_delivery_channel

register_delivery_channel("my-channel", MyDeliveryChannel())
```

The provider has two responsibilities:

1. `capture_current()` returns JSON-serializable addressing/identity data from a
   trusted, session-bound conversation, or `None` outside that channel. Never
   accept arbitrary model-selected recipients. The registry adds `version=1` and
   `channel` to the persisted target.
2. `open(target, session_id)` is an async context manager. Validate current access,
   bind channel-specific context/tools for this session, and yield a `BoundDelivery`
   with `send_final(content, *, delivery_id)`. The stable run ID is available as a
   provider idempotency key when its API supports one. Return a nonempty JSON-serializable receipt only
   after confirmed delivery; raise `DeliveryBlocked` only when no final send was
   attempted. Clean up transport and context bindings on exit.

No scheduler branch or database migration is needed for a different address format.
Unknown channels/target versions fail closed, never fall back to another recipient.
Providers own credential lookup, identity continuity, formatting, and attachment
capabilities. Credentials themselves must never be persisted in the target.

[Telegram's provider](../app/channels/telegram_delivery.py) is the concrete example.
The [delivery tests](../tests/unit/scheduler/test_delivery.py) include a second,
non-Telegram provider using the same persisted outbox and recovery path.
