# Home Dynamic “Try asking” Prompts

## Goal

Make the three Home-page “Try asking” chips useful for the current account without making Home dependent on an available LLM. The category labels remain stable:

- Plan a project
- Inspect a codebase
- Research a topic

Only the underlying prompt text changes dynamically.

## Design

### Request flow

Home renders immediately with the existing hardcoded prompts. After the dashboard snapshot loads, `dashboard.js` requests a separate authenticated endpoint, `/api/dashboard/prompts`, asynchronously. A successful response replaces each chip’s `data-prompt` value and keeps the existing click behavior.

The endpoint resolves the logged-in user from the request. It checks a per-user in-process cache first. On a cache miss, it collects a compact, user-scoped context from:

- Recent knowledge entries: titles plus truncated bodies/tags.
- Recent episodic memories: objectives plus truncated context/outcome summaries.

The endpoint calls the existing profile-resolved LLM client once and asks for exactly one prompt for each fixed category. The model output is parsed and validated before it can reach the browser or cache.

### Cache

Successful LLM results are cached by authenticated user ID for 90 minutes. The TTL is a single server-side setting within the agreed 60–120 minute range. The cache is process-local to avoid a schema migration; a process restart simply causes the next request to regenerate or use fallback prompts.

Failed, unavailable, timed-out, or invalid LLM results are not stored as successful results. This allows a later request to recover when the model becomes available again.

### Response contract

```json
{
  "prompts": [
    {"key": "plan", "label": "Plan a project", "prompt": "..."},
    {"key": "inspect", "label": "Inspect a codebase", "prompt": "..."},
    {"key": "research", "label": "Research a topic", "prompt": "..."}
  ],
  "source": "llm"
}
```

The `key` order is stable. `source` is `llm` for a valid generated response and `fallback` when the hardcoded path is used.

### Validation and fallback

The server accepts a generated response only when it contains exactly three distinct non-empty prompt strings with reasonable length limits, one for each key. Malformed JSON, duplicate content, unexpected structure, or model errors select one random hardcoded prompt from each category.

The fallback pool contains multiple prompts per category. It is selected independently per response, so unavailable-model users do not always see the same three prompts. The fallback is returned without blocking Home and does not prevent future LLM attempts.

Memory and knowledge context is scoped to the authenticated user and truncated before the model call. Model failures are logged server-side without exposing provider errors in the UI.

### Frontend behavior

The initial HTML remains usable without JavaScript or a working model. The asynchronous response updates only prompt metadata; labels and visual layout stay unchanged. If the request fails, the existing hardcoded prompts remain active. All dynamic values are escaped using the existing dashboard rendering conventions.

## Testing

Add focused tests for:

- Valid LLM output and category mapping.
- Per-user cache hits and 90-minute expiry.
- User isolation when building memory/knowledge context.
- Unconfigured, failing, timed-out, malformed, and duplicate LLM responses.
- Random fallback selection with one prompt per category.
- API response shape and authenticated user scoping.
- Home chip updates and preservation of fallback behavior when the async request fails.

Run the targeted tests, Ruff, and the existing full Python test suite before completion.

## Scope boundaries

This feature does not rename categories, add a refresh control, persist prompt suggestions in SQLite, or change the chat submission flow. It also does not send full memory documents to the model; only compact summaries needed to personalize the suggestions are included.
