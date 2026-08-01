# Skills awareness prompt injection

**Date:** 2026-08-01  
**Status:** implemented

## Goal

Match Hermes progressive disclosure: inject a compact skill catalog into the system prompt so agents know what exists, then load full bodies via `use_skill`.

## Behavior

- Injected by `build_system_prompt` via `build_skills_system_prompt(agent_id)` for **every** agent with skill tools
- Includes:
  - Scan/load instructions
  - Compact `<available_skills>` catalog (assigned first, `*`, 80-char desc, cap 48)
  - `SKILLS_GUIDANCE` when `manage_skill` is enabled (even if catalog empty)
- Bodies never inlined — model must call `use_skill`
- `MEMORY_GUIDANCE` gated on `memory` tool (Hermes-style)
- Role `SYSTEM.md` keeps identity/policy only — no duplicated skill/memory how-to

## Files

- `app/runtime/agent/skills_prompt.py`
- `app/runtime/agent/context.py` (`_skills_prompt_section`, memory gate)
- `defaults/coordinator_system.md`, `defaults/agents/{coder,ops,research}/SYSTEM.md` (trimmed)
- `tests/unit/runtime/agent/test_skills_prompt.py`
