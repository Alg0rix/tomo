# Alpha Slice F — Telegram Channel Implementation Plan

> Agentic workers: subagent-driven-development or executing-plans.

**Goal:** Telegram bot inbound → session turn → reply; token encrypted in settings; UI status real.

**Do not** start server. Mock Telegram HTTP in tests. WhatsApp/Discord stretch only if Telegram done early. No G–H except what’s needed for settings.

## Tasks
1. Settings keys `telegram_bot_token` (encrypted) + enabled flag; masked GET
2. `app/channels/telegram.py` — poll or webhook stub with long-poll; map chat → session; call same turn path as web
3. Agent Channels + System Shared channels show connected/needs token
4. Mocked API tests
5. Progress + commit `feat: Telegram bot channel`

## Out of scope
Full multi-tenant, WhatsApp required, scheduler (G).
