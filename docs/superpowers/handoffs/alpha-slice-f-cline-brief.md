# Cline Brief — Alpha Slice F: Telegram

**Repo:** `/home/dev-serv/Project/py-proj/tomo`  
**Plan:** `docs/superpowers/plans/2026-07-26-alpha-slice-f-telegram.md`  
**Slice F only.** No G–H. Do not start the server.

## Goal
Telegram bot: encrypted token; inbound message → agent turn → reply; UI status; mocked tests.

## Requirements
1. Follow plan. Never log token. Reuse chat/turn pipeline.
2. Mark progress F done; commit `feat: Telegram bot channel`.

## Verify
```bash
uv run pytest tests/unit/channels/ tests/unit/runtime/ -q
```
