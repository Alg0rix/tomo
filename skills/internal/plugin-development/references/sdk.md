# Tomo SDK v1 contract

| Surface | Contract |
| --- | --- |
| `setup(api)` | Synchronous entrypoint in plugin.py; must not return an awaitable |
| `api.id`, `api.path` | Stable plugin ID and server-local source directory |
| `api.base_url` | `/plugins/<id>` |
| `api.router` | FastAPI APIRouter; HTTP handlers may be async |
| `api.public_router` | Anonymous FastAPI APIRouter mounted at `/plugins/<id>/public`; explicit opt-in |
| `api.public_base_url` | `/plugins/<id>/public` |
| `api.static_url` | `/plugins/<id>/static`; also available as `plugin.static_url` in both renderers |
| `api.page(path, label)` | Declare navigation; local absolute path, no query/hash/traversal |
| `api.render(request, template, **context)` | Plugin templates plus Tomo base, shared navigation/theme |
| `api.render_public(request, template, **context)` | Standalone plugin template without account/navigation context; `plugin.base_url` points to the public namespace |
| `api.tool(name, description, parameters, handler)` | Object schema, synchronous handler(arguments), namespaced tool ID |
| `api.data_dir` | Persistent plugin data directory |
| `api.user_data_dir(user_id=None)` | Private data; HTTP must pass authenticated user explicitly |
| `api.list_workplaces(*, user_id=None)` | Enabled tunnels, safe metadata and live `online`; active administrators only |
| `api.workplace_status(workplace_id, *, user_id=None)` | Explicit enabled tunnel status; no server-health inference |
| `api.exec_workplace(workplace_id, command, *, timeout=10, user_id=None)` | Synchronous tunnel bash; structured stdout/stderr/exit_code/execution_time; 0 < timeout ≤ 60; never local fallback |
| `api.home_card(handler, *, title=None, size="s", kanji=None, id=None, default_visible=None)` | Synchronous handler(user_id) returning a typed card dict; size s/m/l; optional single-kanji icon; ≤12 cards; stable id; first named widget visible by default; users choose and resize independently; see [Home cards](home.md) |
| `api.background_task(callback, *, interval_seconds=10)` | Register during setup; sync callback(stop_event); runs after commit; cooperative cancellation on disposal; ≤8 serial periodic workers |
| `api.settings.get/set/delete(..., user_id=None)` | Atomic persistent per-account JSON; active account; ≤64 KiB/value; not secret storage |
| `api.capture_notification_target(*, user_id=None)` | Capture owned active channel/session as opaque saved ID; Telegram built-in; no arbitrary recipients |
| `await api.notify(target_id, message, *, user_id=None)` | Recheck active account, session ownership and channel authorization; ≤4000 characters; no automatic retries |
| `api.read_workplace_file(id, path, *, max_bytes=65536, timeout=10, user_id=None)` | Bounded UTF-8 text via POSIX head over tunnel; shell-quoted paths; admin only; ≤1 MiB; content/path/truncated |
| `await api.generate(prompt, *, profile_id=None, max_output_tokens=1024, timeout=60, user_id=None)` | Tool-free API-profile call; active account; ≤32 KiB prompt, ≤4096 output tokens, ≤60s, 30 attempts/account/plugin/hour; recorded usage; subscription profiles rejected |
| `api.starter(label, prompt)` | Home composer prompt chip; ≤4 per plugin |
| `api.on_turn_end(callback)` | Synchronous callback receiving TurnEndContext |
| `api.on_dispose(callback)` | Synchronous cleanup callback |
| `static/` | Public shipped assets at `/plugins/<id>/static/`, automatically mounted while enabled; never private data |
| `skills/<name>/SKILL.md` | Automatically namespaced and discovered while enabled |

Workplace methods recheck an active admin account on every call because core has
no per-user workplace ACL yet. HTTP handlers pass `session_user_id(request)`;
tools/Home cards can use the bound user. Collectors pass the configuring admin's
ID explicitly. No anonymous access, agent approval dispatch, or injected secret
capabilities. Use fixed collection commands, synchronous routes/worker threads,
and cache metrics for Home. `PermissionError` means denied; `ValueError` means
invalid input/unsupported or unavailable workplace; `ConnectionError` means
offline/RPC failure (transport exceptions can propagate). See
[full contract](../../../../docs/plugins.md#tunnel-workplaces).

Workers inherit no account/session context. Pass the configuring user explicitly,
cache collector results for Home, check the stop event and bound I/O. Disposal
waits up to five seconds total, then logs noncooperative workers; Python cannot
kill their threads. No raw core SQL, tunnel RPC or credentials are exposed.

`TurnEndContext` has `session_id`, `agent_id`, `message`, `prompt_tokens`, and
`completion_tokens`. It is an observation, not an instruction to start another
agent turn. Look up the authenticated session owner if storing user-specific
hook data; do not invent a user ID or conflate agent and user identity.

`api.pages`, `api.tools`, `api.skills`, `api.home_cards`, and `api.starters` are registration results for runtime
inspection. Use the public registration methods/directories rather than changing
these collections yourself. Tomo has no BB provider bridge, RPC registration,
core UI replacement, npm build step, cron scheduler, or process isolation in this SDK.
