# Tomo SDK v1 contract

| Surface | Contract |
| --- | --- |
| `setup(api)` | Synchronous entrypoint in plugin.py; must not return an awaitable |
| `api.id`, `api.path` | Stable plugin ID and server-local source directory |
| `api.base_url` | `/plugins/<id>` |
| `api.router` | FastAPI APIRouter; HTTP handlers may be async |
| `api.page(path, label)` | Declare navigation; local absolute path, no query/hash/traversal |
| `api.render(request, template, **context)` | Plugin templates plus Tomo base, shared navigation/theme |
| `api.tool(name, description, parameters, handler)` | Object schema, synchronous handler(arguments), namespaced tool ID |
| `api.data_dir` | Persistent plugin data directory |
| `api.user_data_dir(user_id=None)` | Private data; HTTP must pass authenticated user explicitly |
| `api.home_card(handler, *, title=None, size="s", kanji=None)` | Synchronous handler(user_id) returning a typed card dict; size s/m/l; optional single-kanji icon; ≤2 cards; see [Home cards](home.md) |
| `api.starter(label, prompt)` | Home composer prompt chip; ≤4 per plugin |
| `api.on_turn_end(callback)` | Synchronous callback receiving TurnEndContext |
| `api.on_dispose(callback)` | Synchronous cleanup callback |
| `static/` | Automatically mounted while enabled |
| `skills/<name>/SKILL.md` | Automatically namespaced and discovered while enabled |

`TurnEndContext` has `session_id`, `agent_id`, `message`, `prompt_tokens`, and
`completion_tokens`. It is an observation, not an instruction to start another
agent turn. Look up the authenticated session owner if storing user-specific
hook data; do not invent a user ID or conflate agent and user identity.

`api.pages`, `api.tools`, `api.skills`, `api.home_cards`, and `api.starters` are registration results for runtime
inspection. Use the public registration methods/directories rather than changing
these collections yourself. Tomo has no BB provider bridge, RPC registration,
core UI replacement, npm build step, or background-service scheduler in this SDK.
