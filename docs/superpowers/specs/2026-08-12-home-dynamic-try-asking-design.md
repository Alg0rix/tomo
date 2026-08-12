# Home Dynamic “Try asking” Prompts

## Goal

Make the three Home-page “Try asking” chips useful for the current account without making Home dependent on an available LLM. Unlike an earlier draft of this spec, the category identity is **not** fixed: `key`, `label`, and `prompt` are all dynamic. The static, no-JS page still renders the classic three chips (Plan a project / Inspect a codebase / Research a topic) as its first-paint baseline; JavaScript may replace all three fields once a personalized or fallback response arrives.

## Design

### Request flow

Home renders immediately with the existing hardcoded chips (unchanged markup, unchanged labels/prompts — this is the pre-JS/first-paint baseline only). After the dashboard snapshot loads, `dashboard.js` independently (non-blocking) requests a separate authenticated endpoint, `/api/dashboard/prompts`. A successful response replaces each chip's label text and `data-prompt` value **positionally** (response array index 0/1/2 → chip index 0/1/2 — there is no fixed key to match against) and keeps the existing click behavior.

The endpoint resolves the logged-in user from the request. It checks a per-user in-process cache first. On a cache miss, it collects a compact, user-scoped context from:

- Recent knowledge entries (top 5, most recently updated): title, body truncated to 160 chars, tags.
- Recent active episodic memories (top 5, most recent): objective, context_summary and outcome_summary each truncated to 160 chars.

The endpoint calls the existing profile-resolved LLM client once (`app.runtime.llm.get_llm()`) and asks for exactly three JSON objects, each with its own `key`, `label`, and `prompt` — the model chooses categories freely based on the user's context; there is no fixed plan/inspect/research taxonomy on this path. The model output is parsed and validated before it can reach the browser or cache.

### Cache

Successful LLM results are cached by authenticated user ID for 90 minutes (`_CACHE_TTL_S = 90 * 60`, monotonic clock — same pattern as `app/runtime/llm/context_window.py`). The cache is process-local to avoid a schema migration; a process restart simply causes the next request to regenerate or use fallback prompts. A `clear_dashboard_prompts_cache()` helper resets it for tests.

Failed, unavailable, timed-out, or invalid LLM results are not stored as successful results. This allows a later request to recover when the model becomes available again.

### Response contract

```json
{
  "prompts": [
    {"key": "sprint-plan", "label": "Plan the sprint", "prompt": "..."},
    {"key": "dep-audit", "label": "Audit dependencies", "prompt": "..."},
    {"key": "market-scan", "label": "Scan the market", "prompt": "..."}
  ],
  "source": "llm"
}
```

`key`/`label` are model-chosen (LLM path) or pool-chosen (fallback path) and vary response to response — nothing about them is stable across requests. Array order is meaningful only as "which chip slot to update"; `source` is `llm` for a valid generated response and `fallback` when the hardcoded pool is used.

### Validation and fallback

The server accepts a generated response only when it is a JSON array of exactly 3 objects, each with:

- `key`: non-empty string, ≤40 chars
- `label`: non-empty string, ≤60 chars
- `prompt`: non-empty string, ≤300 chars

...and all 3 `key` values are distinct (case-insensitive) and all 3 `prompt` values are distinct (normalized whitespace comparison). Malformed JSON, wrong item count, missing/oversized fields, duplicate keys, duplicate prompt content, unconfigured LLM (`LLMConfigError`), a request that exceeds a 12s timeout (`asyncio.wait_for`), or any other model error all select a fallback response instead — none of these raise to the endpoint caller.

The fallback pool is a flat list of ~12 hardcoded `{key, label, prompt}` triples spanning varied categories (not restricted to plan/inspect/research). Three distinct entries are chosen at random (`random.sample`) independently per response, so unavailable-model users do not always see the same three chips. The fallback is returned without blocking Home, is never cached, and does not prevent future LLM attempts.

Memory and knowledge context is scoped to the authenticated user (`store.list_knowledge_entries(user_id=...)`, `store.list_episodes(user_id=..., state="active")`) and truncated before the model call. Model failures are logged server-side (`logging`) without exposing provider errors in the UI.

### Frontend behavior

The initial HTML remains usable without JavaScript or a working model — the SSR chips are real, clickable, and never removed while the async request is in flight or if it fails. The asynchronous response updates only chip label text and `data-prompt`; it is fetched and applied independently of the existing `/api/dashboard/data` load path and does not block it. If the request fails, the existing hardcoded chips remain active, silently (no toast/error UI). Label and prompt are written via `textContent` / `dataset.prompt` (never `innerHTML`), so no manual HTML-escaping step is needed — this satisfies the same escaping guarantee the rest of `dashboard.js` gets from `Tomo.escapeHtml` when building HTML strings.

## Testing

Add focused tests for:

- Valid LLM output → 3 validated `{key,label,prompt}` items, `source: "llm"`.
- Per-user cache hits and 90-minute expiry (monotonic clock).
- User isolation when building memory/knowledge context (two users never see each other's summaries).
- Unconfigured (`LLMConfigError`), failing, timed-out, malformed-JSON, wrong-count, duplicate-key, and duplicate-prompt LLM responses all fall back.
- Random fallback selection: 3 distinct entries per call, pool has ≥3 entries, no crash when sampling repeatedly.
- API response shape and authenticated user scoping (`/api/dashboard/prompts` requires auth, returns the contract above).
- Frontend chip update is positional (index-based), not key-based, and preserves fallback (SSR) chips when the async request fails — covered by reading `dashboard.js` logic; no JS test runner exists in this repo so this is verified by code inspection, not an automated test.

Run the targeted tests, Ruff, and the existing full Python test suite before completion.

## Scope boundaries

This feature does not add a refresh control, persist prompt suggestions in SQLite, or change the chat submission flow. It does not send full memory documents to the model; only compact summaries needed to personalize the suggestions are included. It does not rename or restyle the chip UI itself — only the text content and click payload change. (Earlier draft language about categories staying fixed no longer applies: `key` and `label` are dynamic by design.)
