# Channels, settings, approvals, and limits

Use this for Telegram, general settings, learning controls, memory job settings,
permissions defaults, and execution limits. Read
[CLI configuration](configuration-cli.md) for data input and runtime reloads.

## Inspect supported settings

Run `tomo config settings show --json` before changing values. Update only known
keys and requested fields. Settings are installation-wide; a setting is not an
agent record, session override, environment variable, or plugin-specific API.
Preserve current values outside the request and read back the result.

| Area | Relevant existing keys |
| --- | --- |
| Telegram | `telegram_enabled`, `telegram_bot_token`, `telegram_allowed_chat_ids`, `telegram_rich_messages` |
| Voice transcription | `telegram_transcription_enabled`, `telegram_transcription_base_url`, `telegram_transcription_model`, `telegram_transcription_api_key` |
| Agent limits | `max_tool_iterations`, `concurrency_limit`, `llm_timeout_seconds` |
| Learning loop | `learning_enabled`, `learning_review_profile_id`, `learning_cooldown_sec`, `learning_memory_nudge_turns`, `learning_skill_nudge_iters` |
| Memory | `memory_vault_enabled`, `memory_extraction_profile_id`, `memory_consolidation_enabled`, `memory_consolidation_cron` |
| Approval defaults | `approvals_mode`, `approvals_timeout`, `approvals_deny` |
| Display/history | `theme`, `public_history` |

Treat the installed settings readout as authoritative if keys differ. Inspect
current defaults and consequences instead of increasing all limits to cure a
single failure. Larger iteration/timeout/concurrency settings increase work,
latency, or resource use; they do not grant missing tools or repair auth.

## Configure Telegram

Establish the existing bot, allowed chat IDs, and intended behavior. Store token
updates through stdin or a protected JSON file. Do not echo bot tokens or
transcription credentials; a public `_set` flag indicates only a saved value.

```bash
tomo config settings update --data @/absolute/telegram-settings.json --json
tomo config settings update --set telegram_allowed_chat_ids='["123456789"]' --json
tomo config settings update --set telegram_rich_messages=true --json
```

Replace the example ID with actual intended chats, preserving other allowed
chats. IDs normalize as non-zero numeric strings; an empty allowlist is not an
authentication repair. Bot token, enablement, and allowlist are separate values.
Channel configuration does not assign a workplace or automatically enroll an
agent in a chat/session.

For voice input, configure the actual transcription service's base URL, model,
key, and enablement. Do not assume the active text-model profile supplies that
service's credential. Saved configuration does not prove transcription works.
Verify with an authorized short voice input only when that test is in scope.

Apply connection changes to the running coordinator when needed, then inspect
channel status and receive/respond in an intended chat. Do not send unsolicited
messages to contacts/groups merely to test setup. If bot polling fails, inspect
short relevant logs, outbound access, token validity, allowed chat, and whether
another deployment is polling the same bot. Do not install a duplicate instance
or repeatedly regenerate tokens as a first diagnostic step.

## Approval modes and scope

Stored modes are `manual`, `smart`, and `off`; the UI calls `off` **Auto**.
Session controls such as `/auto` can override the installation default. A global
CLI settings update does not establish that a chat's override changed.

Change approval policy only when the user requests that policy change. Do not
switch to Auto or remove deny rules to bypass a blocked action. Worker tools,
workspace scopes, OS access, and action approvals remain separate controls.
When an operation is blocked, report the actual gate and required prerequisite.

## Learning and memory settings

`learning_review_profile_id` and extraction profile settings are profile IDs,
not provider model strings. Check that the selected profile is enabled/usable.
Enabling learning permits eligible background review; it does not retroactively
prove a skill was learned. Inspect the created library skill or stored memory.

Memory consolidation cron is validated as five-field cron. A change to a
runtime job may need the coordinator lifecycle to apply. Read
[memory and knowledge](memory-knowledge.md) for choosing what to store and where.
Do not change data retention/sharing settings such as `public_history` just to
make a report visible; use the appropriate artifact/session delivery path.
