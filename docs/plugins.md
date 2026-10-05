# Live Tomo plugins

Plugins add multiple pages, HTTP handlers, and domain tools to the running Tomo
server. Install, enable, disable, reload, and update do not restart Tomo. Deploying this
runtime itself requires one normal application restart.

Open **Plugins** in navigation (`/extensions`). Discover searches your connected
marketplace catalogs; Installed shows runtime status, pages, tools, and lifecycle
controls. Use **New plugin** (`/extensions#create`) to describe a plugin, pick an
enabled agent and local build folder, and start a new development conversation.
Plugin ideas are generated from the user’s activity and memory using the same
auxiliary model as Home prompt suggestions. They are cached separately per user
and installed-plugin set. More ideas regenerates them; unavailable models use
random fallback ideas. Suggestions fill the composer without sending it. The selected agent uses its
existing model and permission settings. **Install from source** accepts local
paths, catalog references, or public GitHub URLs, including ref/subdirectory
options. **Plugin guide** (`/extensions/guide`) explains available SDK surfaces.

Administrators can register a
server-local source directory, enable it, and reload after editing. Installation
registers a path; it does not copy files or execute the plugin. Sources must stay
available. Token Monitor and Task Board are installed from the independent
[official plugin repository](https://github.com/Alg0rix/tomo-plugins), not bundled
in Tomo core. After installation their URLs are `/plugins/token_monitor/` and
`/plugins/kanban/`; they use the same enable, disable, reload, and uninstall
operations as any other plugin. Fresh installations choose plugins from Discover.
Upgrade moves old bundled registrations to downloaded official sources once,
preserving enabled flags and data. If the download fails, Tomo starts and shows
an actionable plugin error; retry Enable after connectivity is restored.
The old module APIs, CLI resource, gallery, and URL aliases have been removed.
Upgrade migrates existing enabled flags once and keeps usage history.

Agents have the `plugin_manager` tool and dedicated `plugin-development`,
`plugin-management`, and `plugin-publishing` skills. For
example: “Develop a money plugin, install it, and enable it.” Plugin management
requires an administrator account. Domain tools appear in the normal agent Tools
settings and are discovered on subsequent turns. An already running turn retains
its initial tool schema list; disabled plugin tools refuse execution immediately.

CLI commands contact the running server, using `TOMO_API_KEY` from the environment
(an administrator API key) and `TOMO_URL` (default `http://127.0.0.1:8787`):

```sh
tomo plugins marketplaces refresh tomo-official
tomo plugins install money@tomo-official
tomo plugins enable money
tomo plugins reload money
tomo plugins disable money
tomo plugins uninstall money
```

`--url URL` can be supplied before the subcommand. Use HTTPS when contacting a
remote server. These commands do not load a second plugin runtime in the CLI.
The equivalent authenticated API is `GET /api/plugins`,
`POST /api/plugins/install` with `{"path":"..."}`, and
`POST /api/plugins/<id>/<enable|disable|reload|update|uninstall>`.

## Updates by source revision

Open **Installed** to check plugin sources automatically; **Check updates** forces
another check. Automatic checks use a five-minute server cache. Git branch
installs compare the installed commit SHA with the branch's current SHA, so
**authors do not need to bump `version` for every source change**. Installed
version identifies the running snapshot; Repository version comes from the
selected commit’s `tomo-plugin.json`. An available version is displayed before
applying the update, and becomes the installed version only after a successful
update. The check
validates the candidate manifest at that exact revision without downloading the
repository, importing Python, or executing setup. Network and compatibility
failures are shown as check errors, never as “up to date.”

Choose **Update** to download the checked commit into a new managed source folder.
Enabled plugins build a replacement and swap pages, tools, and usage skills live.
Disabled plugins update their source without executing it or becoming enabled.
Data and agent settings are preserved. A failed activation or registry save
keeps the previous source and running instance; a busy plugin returns a conflict.
The branch moving during a download cannot change the selected revision.

Git tags and commit references are pinned and stay unchanged. Local `path:`
sources use local edits or `git pull`, then **Reload code**. Updates follow the
recorded repository/ref/subdirectory independently of catalogs, including after
the original marketplace is removed. Refreshing a catalog does not apply updates
or redirect an installed plugin to a different source. The declared version is
still useful release metadata and must match a catalog when first installing.

```sh
tomo plugins outdated
tomo plugins update money
```

The agent tool uses `plugin_manager(action="outdated")` and
`plugin_manager(action="update", id="money")`. The administrator API is
`POST /api/plugins/check-updates` with `{"force": true}` and
`POST /api/plugins/<id>/update`. Update checks are metadata-only; applying an
update executes new code only for an already enabled plugin. Old successful
source snapshots are retained on disk; rejected candidate downloads are removed.

## Icons

Set an optional `"icon": "wallet"` in `tomo-plugin.json` and catalog listings.
Tomo uses locally vendored open-source Lucide SVGs, inherited theme colors, and
consistent sizes in cards, details, System, and the sidebar. Supported names:
`puzzle`, `wallet`, `chart-column`, `columns-3`, `library`, `notebook`, `calendar`,
`globe`, `code`, `briefcase`, `heart`, and `layout-grid`. The default is `puzzle`.
Icon metadata is a name, never arbitrary SVG markup or a remote URL.

## Package and SDK

```text
my_plugin/
  tomo-plugin.json
  plugin.py
  templates/
    overview.html
    details.html
```

```json
{"id":"my_plugin","name":"My plugin","description":"A domain feature","version":"0.1.0","sdk_version":1}
```

```python
from fastapi import Request

def setup(api):
    api.page("/", "Overview")
    api.page("/details", "Details")

    @api.router.get("/")
    def overview(request: Request):
        return api.render(request, "overview.html")

    @api.router.get("/details")
    def details(request: Request):
        return api.render(request, "details.html")

    api.tool("summary", "Read this user's summary",
             {"type": "object", "properties": {}}, lambda arguments: {"total": 0})
```

Templates can extend Tomo's `base.html`. `plugin.base_url` and `plugin.pages` are
available in rendered context. Routes are automatically mounted under
`/plugins/<id>` and require Tomo authentication. Register application routes
through `api.router`; do not modify global Tomo routers. A `static/` directory is
served publicly at `/plugins/<id>/static/` and follows plugin enablement. Put
only shipped assets there, never private data. `api.static_url` and
`plugin.static_url` provide this asset URL in both renderers.
Register `api.on_turn_end(callback)` to receive a `TurnEndContext`
with session, agent, message, and token counts. WebSockets are not supported in this version.
Tools have ids `plugin__<id>__<name>` and synchronous handlers returning strings
or JSON-serializable values. Validate tool arguments in the handler.

`api.user_data_dir()` returns a current-agent-user data directory. For HTTP
handlers use `api.user_data_dir(session_user_id(request))`. The Money example
stores amounts as integer minor units and uses the same ledger in both its pages
and agent tools. Agents use Tomo's existing models and conversation; the plugin
needs no separate AI key or agent loop.

### Tunnel workplaces

Reuse existing paired Tomo connectors for monitoring; no separate SSH connection
or agent turn is needed. These synchronous SDK methods support **enabled tunnel
workplaces only**:

- `api.list_workplaces(*, user_id=None)` returns safe metadata: `id`, `name`,
  `kind`, `online`, `hostname`, `version`, `last_seen_at`. No pairing codes or credentials.
- `api.workplace_status(workplace_id, *, user_id=None)` returns the same metadata
  for an explicit ID. `online` means connector connected, not server healthy.
- `api.exec_workplace(workplace_id, command, *, timeout=10, user_id=None)` runs
  bash in the connector's default work root. Returns the connector result dict
  (`stdout`, `stderr`, `exit_code`, `execution_time`); nonzero exits are normal
  results. Timeout must be finite, greater than zero and at most 60 seconds.

Workplaces currently have no per-user sharing ACL, so all three methods require
an **active administrator**, checked on every call. Tools/Home callbacks may use
Tomo's bound user; HTTP handlers must pass `session_user_id(request)` from
`app.core.deps`. Background collectors must supply the actual administrator ID
that configured monitoring, not an anonymous fallback or user-supplied identity.
Disabled or unknown accounts raise `PermissionError`. Missing, disabled, local,
or SSH workplaces raise `ValueError`; offline tunnels and failed RPC responses
raise `ConnectionError`. Transport exceptions may also propagate. Execution
never falls back to the Tomo host. This is a trusted plugin service, not an agent
tool dispatch: agent command approvals and secret capabilities are not applied.
Use fixed metric-collection commands, not arbitrary commands from visitors.

```python
from app.core.deps import session_user_id

@api.router.get("/servers")
def servers(request: Request):
    return api.list_workplaces(user_id=session_user_id(request))

# In a collector worker, with the configuring admin's ID:
result = api.exec_workplace(server_id, "uptime", user_id=admin_id, timeout=5)
```

Use synchronous routes or offload these calls to a worker thread; do not block
an async HTTP handler/the server event loop. Collect separately and cache metrics;
Home card handlers should only read that cache within their shared deadline.
Handle offline/failed collection as stale data. Stop collector threads with
`api.on_dispose()` when disabling or reloading a plugin.

### Background collectors

`api.background_task(callback, *, interval_seconds=10)` registers up to eight
serial periodic workers during `setup`. `callback(stop_event)` is synchronous;
its first call starts **after successful activation/registry persistence**, not
while preparing a speculative reload. Subsequent calls wait 1–3600 seconds after
the preceding callback returns. Exceptions are logged and retried next interval.
Failed setup/save never starts workers. On reload/update, old workers are
cancelled before replacement workers start; disable, uninstall and server shutdown
also signal cancellation. Workers inherit **no user/session context**.

```python
import threading

def setup(api):
    def collect(stop: threading.Event):
        if stop.is_set():
            return
        # Read configured admin IDs and use short timeouts for remote I/O.
        # Update plugin-owned cached metrics; Home only reads this cache.
    api.background_task(collect, interval_seconds=10)
```

Cancellation is cooperative: check the event, use `stop.wait(delay)` instead of
`sleep`, and bound all I/O. Disposal signals all workers and waits up to five
seconds total before cleanup callbacks. Python cannot forcibly kill a blocked
thread; a noncooperative worker is logged and may outlive disposal. SDK remote,
notification and generation calls reject disposed instances. Never depend on an
unbounded callback being stopped or on long-running workers holding manager leases.
Task runtime errors do not deactivate the plugin. No cron, process isolation,
async callbacks or multi-worker scheduling is implied.

### Persistent per-account settings

`api.settings.get(key, default=None, *, user_id=None)`,
`api.settings.set(key, value, *, user_id=None)` and
`api.settings.delete(key, *, user_id=None)` store JSON in plugin-owned SQLite at
`api.data_dir / "sdk.sqlite3"`. Values are isolated by account and survive
reload/update/uninstall, with atomic writes across threads. Keys contain 1–128
characters; each JSON value is at most 64 KiB. NaN/non-JSON values are rejected.
These methods require an active Tomo account. For HTTP, pass the authenticated
account ID; never accept an owner ID from the request body. Worker threads must
pass the configuring account explicitly. This is settings storage, **not a secret
vault**; do not put credentials here.

```python
api.settings.set("monitor", {"interval": 10, "server_id": server_id}, user_id=uid)
config = api.settings.get("monitor", {}, user_id=uid)
```

### Captured-channel notifications

`api.capture_notification_target(*, user_id=None)` captures the current owned
session's channel destination and returns an opaque, persistent ID. Capture from
an agent tool while the channel turn is bound, not from an arbitrary HTTP body.
`await api.notify(target_id, message, *, user_id=None)` sends 1–4000 characters to
that saved destination. Background workers can use `asyncio.run(api.notify(...))`
with the configuring account ID. No chat IDs, bot tokens or arbitrary recipients
are accepted. Ownership of the saved session and active account are rechecked;
the channel also checks current destination authorization and bot identity.
Deleting the originating session invalidates its targets.

Currently Telegram is the built-in delivery channel. Browser-only sessions have
no notification destination and capture raises `ValueError`; this does not add a
web notification inbox. Denied/revoked targets raise `PermissionError` or the
channel's `DeliveryBlocked`; transport failures propagate. The receipt is the
channel's result dict. Sends have no automatic retries or exactly-once guarantee;
plugins should deduplicate alerts and treat uncertain sends carefully.

### Bounded remote text reads

`api.read_workplace_file(workplace_id, path, *, max_bytes=65536, timeout=10,
user_id=None)` returns `{"path": ..., "content": ..., "truncated": ...}`.
It reuses tunnel bash with shell-quoted paths and POSIX `head`; it does not download
the entire file. `max_bytes` is 1–1048576. Relative paths use the connector work
root. UTF-8 decoding replaces incomplete/invalid characters; this is a text API,
not a binary download API. Nonzero reads raise `OSError`. The same active-admin,
timeout, explicit-workplace and no-local-fallback rules as `exec_workplace` apply.
Plugins must choose safe log/config paths and avoid leaking sensitive contents.

### One-shot LLM generation

`await api.generate(prompt, *, profile_id=None, max_output_tokens=1024, timeout=60,
user_id=None)` uses Tomo's configured API model profile (explicit enabled ID, or
global default). It returns `content`, `prompt_tokens` and `completion_tokens`.
No tools, agent loop or extra API key are needed. Calls require an active account;
HTTP/collectors pass the authenticated/configuring account ID explicitly.

Guards: nonempty prompt up to 32 KiB UTF-8, output limit 1–4096 tokens sent to the
provider, and a timeout greater than zero and at most 60 seconds. There is no
hourly call cap. Provider-reported successful usage enters the
existing `usage_events` ledger under `agent_id="plugin:<id>"`, with zero agent turns
and an account-namespaced synthetic session; prompt contents are not recorded.
Limits depend on provider enforcement and are not a monetary billing ceiling.
Timed-out/failed requests may still incur provider costs not reported in usage.
Clients are closed after success, error, or cancellation.

Subscription profiles are explicitly rejected for bounded generation: their
backend does not support the output cap. There is no silent unbounded fallback.
For synchronous plugin tools/worker threads use `asyncio.run(api.generate(...))`;
async HTTP handlers should await it directly.

### Agent turns and routines

`await api.agent(prompt, *, user_id=None, agent_id=None)` starts one real agent
turn — tools, memory, and the account's current grants — in a new session owned
by that account. It returns `session_id`, `agent_id`, and `status="started"`
once the turn is accepted, and does not wait for the model. The agent defaults
to the account's coordinator. Prompt limit is 32 KiB. There is no hourly cap.
Calls from `on_turn_end` are rejected so a finished turn cannot schedule the
next one.

`api.schedule(name, prompt, *, when, user_id=None, agent_id=None)` creates a
routine the account owns and can pause or delete in Routines. `when` is a
schedule string such as `every 30m` or `0 9 * * *`. The prompt is at most 4000
bytes. Each fire runs in a fresh
session under the account's grants at that moment. `api.schedules(user_id=None)`
lists only routines this plugin created. `api.unschedule(schedule_id, user_id=None)`
deletes one of those; another plugin's routine is unavailable.

Worker threads use `asyncio.run(api.agent(...))`. While Tomo is running, that
call hops onto the server loop so the turn is not cancelled when the temporary
loop closes. `api.schedule` is synchronous. Pass the account id explicitly from
workers and HTTP handlers.

### Public landing pages and forms

Use `api.public_router` to opt individual handlers into anonymous access at
`/plugins/<id>/public`. `api.router` continues to require login. Public routes
can serve a plugin's own landing page, survey, or submission endpoint:

```python
from fastapi import Request
from pydantic import BaseModel, Field

class SurveyResponse(BaseModel):
    answer: str = Field(min_length=1, max_length=1000)

def setup(api):
    @api.public_router.get("/")
    def landing(request: Request):
        return api.render_public(request, "survey.html")

    @api.public_router.post("/responses")
    def submit(body: SurveyResponse):
        # Validate and save the response in plugin-owned storage here.
        return {"accepted": True}
```

`api.public_base_url` is `/plugins/<id>/public`. In `render_public`,
`plugin.base_url` and `plugin.public_base_url` both point there, so a template
can submit to `{{ plugin.base_url }}/responses`. Write standalone HTML (or
extend your own plugin template) for your landing page. Public rendering does
not load Tomo's account or navigation context; `api.render` remains the renderer
for authenticated dashboards.

Put CSS, JavaScript, and images in `static/`; they are served publicly at
`api.static_url` (`/plugins/<id>/static`). In either renderer, use
`{{ plugin.static_url }}/style.css` for assets. The `/public`
namespace is reserved when the plugin registers public routes; enable
and reload fail if `api.router` also defines `/public` paths. Missing public
pages return a plain "Page not found" page to browsers and JSON to API clients. Public
routes and assets follow the same enable, reload, disable, and uninstall
lifecycle as private routes, without a restart. You can optionally register
`api.page("/public/", "Survey")` to link the public landing page from Tomo.

Public handlers have no required Tomo identity. Do not use the anonymous
`session_user_id(request)` fallback (`web`) as a survey owner or store all
visitors in `api.user_data_dir()`. Use plugin-owned storage with explicit survey
and response identifiers. Authors are responsible for input validation, abuse
limits, and any additional access tokens needed by public submissions. Keep
administration and response exports on `api.router` with ownership checks.
The [nginx deployment example](deployments.md#6-reverse-proxy) applies request
size and rate limits at the proxy. Direct deployments need equivalent limits
in their proxy or plugin handlers.

### Home cards

`api.home_card(handler, title=None, size="s", kanji=None, id=None, default_visible=None, refresh_seconds=None)` adds a room card to Home's
Rooms grid; `handler(user_id)` is synchronous, runs per Home load under a 2.5s
shared deadline with the user bound, and returns a typed dict (`status`,
`metric`, `caption`, `trend`, `stats`, `chart`, `spark`, `ring`, `heatmap`,
`bars`, `columns`, `timeline`, `checklist`, `list`, `tags`, `quote`, `meter`,
`actions`, `empty`). `size` is `s`, `m`, or `l`; `kanji` sets a one-character
room icon. Core validates and renders it (`app/services/home.py::normalize_card`);
unknown fields and non-local links are dropped. At most twelve cards per plugin.
Give each widget a stable `id` (lowercase letters, digits, `_`, `-`, starting
with a letter, up to 64 characters). Keys become `<plugin-id>:<id>`; unnamed
cards retain `<plugin-id>:<index>`. Only the first named card is visible by
default; `default_visible=True` or `False` overrides this. Unnamed cards retain
their previous visible defaults. Users choose widgets individually with Home →
Add widget, and arrange, remove, or resize them with Arrange. Selection, order,
and size are saved per account. Removing a widget preserves plugin data and its
saved size; it remains available in the picker. Mobile stacks cards with automatic
height while keeping desktop sizes saved.
`api.starter(label, prompt)` adds up to four composer prompt chips. Plugins
without a card get a door card. Full field table: the plugin-development
skill's `references/home.md`. Guard calls with `hasattr(api, "home_card")` when
supporting older Tomo releases.

Opt into periodic updates with `refresh_seconds` (integer seconds, 5–3600):

```python
api.home_card(monitor_card, id="server-monitor", title="Server monitor",
              refresh_seconds=10)
```

Home refreshes only selected widgets whose intervals are due, while the tab is
visible and Arrange mode is off. Updates preserve widget order and size. Failed
updates keep the last successful readings and show a **Stale** badge until a
successful refresh. Omitting the interval keeps refresh-on-load behavior.
The authenticated `GET /api/home/cards?keys=<plugin-id>:<card-id>` endpoint
returns normalized cards for requested widgets that opted into refresh.
Keep the handler a cheap read of cached metrics; collect monitor data separately.

Use `api.on_dispose(callback)` for cleanup. Callbacks run on replacement,
disable, uninstall, shutdown, or failed activation. Imports have a fresh package
namespace on every reload; use relative imports for plugin helpers. Setup is
synchronous and should only register contributions. Route ASGI lifespans are not
started; do not depend on a plugin FastAPI lifespan hook.

## Lifecycle and trust

Enabled state persists in `$TOMO_HOME/plugins/registry.json`. Data lives in
`$TOMO_HOME/plugins/data/<id>` and survives source changes and uninstall.
Reload builds a replacement before swapping routes/tools. Failed setup retains
the previous running plugin. Lifecycle mutations return conflict while a plugin
is handling a request or tool; retry when it finishes. Cleanup is best-effort.
Plugin code must not mutate durable data during setup: arbitrary side effects
cannot be rolled back after failed activation.

Python plugins execute in-process with server privileges. Authentication and
user-scoped SDK helpers protect ordinary plugin use; they do not sandbox trusted
Python code. This implementation targets Tomo's normal single-server-process
configuration. Multi-worker deployments need coordinated activation before using
live plugins. Declare dependencies in the plugin package and use Sync dependencies before
activation. There is no file watcher; explicitly reload
after source edits. Catalog discovery and GitHub downloads are described below.


## Official plugins and community marketplaces

[Alg0rix/tomo-plugins](https://github.com/Alg0rix/tomo-plugins) is the home for
Tomo's official plugin source and its own independent `marketplace.json` catalog.
[Alg0rix/tomo-marketplace](https://github.com/Alg0rix/tomo-marketplace) accepts
community listing submissions through pull requests. Community authors keep
source code in their own repositories. **Official plugins never need submission
to the community marketplace.** Installed source packages remain available offline.

Open `/settings/marketplaces` to manage catalogs. Tomo Official (`tomo-official`)
and Tomo Community (`tomo-community`) are separate default sources. Refresh
fetches and validates metadata, caches it, and never installs, updates, or runs
plugin code. A failed refresh keeps the last validated catalog. Adding a custom
catalog uses an HTTPS manifest URL, `git:https://github.com/owner/repo@ref`, or
`path:/server/directory`. The manifest's id is its identity; default ids and names
are reserved. Removing a custom marketplace leaves installed plugins untouched.

```sh
tomo plugins marketplaces list
tomo plugins marketplaces refresh tomo-official
tomo plugins marketplaces refresh tomo-community
tomo plugins search money
tomo plugins install money@tomo-official
tomo plugins enable money

# Direct distribution: no marketplace registration is needed.
tomo plugins install https://github.com/Alg0rix/tomo-plugins.git --subdirectory plugins/money --ref main
```

Local directory installs continue to work. For remote installs Tomo supports
public HTTPS GitHub repositories, resolves a branch/tag/commit to a commit SHA,
and downloads that exact revision. It validates catalog identity and version,
rejects archive traversal and symlinks, records source/commit provenance, and
registers the plugin **disabled**. Enable is the separate step that executes code.
Source downloads do not run package managers, Git hooks, or plugin setup.
Sync dependencies is a separate action; enabled updates sync newly required
packages before activation. Managed source edits stay at the downloaded commit;
reload does not fetch updates. Private repositories and automatically applying updates are
not supported in this version.

`GET /api/marketplaces` lists sources; `POST /api/marketplaces` adds a source;
`POST /api/marketplaces/<id>/refresh` refreshes metadata;
`DELETE /api/marketplaces/<id>` removes a custom source. Browse cached entries at
`GET /api/plugins/catalog?q=...`. `POST /api/plugins/install` accepts a `path`
(source spec), optional `subdirectory`, and optional `ref`. The `plugin_manager`
agent tool exposes the same marketplace and install operations.

Catalog format:

```json
{
  "schema_version": 1,
  "id": "my-catalog",
  "name": "My Catalog",
  "plugins": [{
    "id": "my_plugin", "name": "My Plugin", "description": "What it does",
    "version": "0.1.0", "sdk_version": 1, "author": "author",
    "source": {"type": "git", "url": "https://github.com/author/repository.git",
      "ref": "v0.1.0", "subdirectory": "plugins/my_plugin"}
  }]
}
```

## Plugin usage skills

A package can include `skills/<name>/SKILL.md` with frontmatter `name` and
`description`, plus supporting `references/`, `scripts/`, or `assets/`. These
read-only packages enter the normal skill catalog when the plugin is enabled.
Their IDs are `plugin__<plugin-id>__<skill-id>` and their source is
`plugin:<plugin-id>`. Agents discover them with `list_skills` and load instructions
or references with `use_skill`. Skill availability and agent tool permissions
remain separate. Reload refreshes the skill entrypoints; failed reload keeps the
previous active entrypoints. Disable and uninstall remove plugin skills from
discovery. Skill files must stay inside the plugin package.

Core ships the [authoring](../skills/internal/plugin-development/SKILL.md),
[management](../skills/internal/plugin-management/SKILL.md), and
[publishing](../skills/internal/plugin-publishing/SKILL.md) procedures. Complete
plugins, domain usage skills, and feature tests belong in
[tomo-plugins](https://github.com/Alg0rix/tomo-plugins). Core contains no example
plugin packages.

## Python dependencies and uv

Declare registry packages as PEP 508 requirements in `requirements.txt` (one per
line, comments allowed), or `[project].dependencies` in `pyproject.toml`. If both
files exist, `requirements.txt` is authoritative. Use tested version ranges or
pins. Dependency names are distribution names: `cv2` is supplied by
`opencv-python-headless` for servers. Declare `onnxruntime` only when importing
it; OpenCV can load ONNX directly. Model weights and system libraries are not
Python dependencies and must be documented separately.

```text
opencv-python-headless
numpy
```

Installed shows missing or incompatible declared dependencies. **Sync
dependencies** resolves requirements for all installed plugins with `uv`, using
Tomo's actual Python interpreter. It preserves core and already imported distribution versions,
downloads prebuilt wheels only, and installs non-core packages in a fresh
`$TOMO_HOME/plugins/dependencies/env-*/site-packages` overlay. It never runs a
plugin entrypoint or enables a disabled plugin. Core imports take precedence. Overlay packages not yet imported can be upgraded
during sync; loaded versions remain protected.
URL/local dependencies, requirements-file includes, package-manager options,
source builds, and wheel `.pth` startup execution are unsupported.

The resolved lock and active environment pointer persist outside the core venv.
Core `uv sync` cannot prune them. `tomo update` restores registered plugin
requirements using the newly synced interpreter before restarting the service.
Python/core changes invalidate an incompatible overlay and require resync. A
resolution/install failure leaves the prior environment active. Shared Python
means incompatible package versions cannot coexist; conflicting upgrades are
rejected instead of replacing loaded packages. A fresh interpreter during
`tomo update` can resolve overlay upgrades before plugin activation. Use compatible requirements or
move that workload to an external worker environment. Plugin data is unaffected.

```sh
tomo plugins sync-dependencies vehicle_cctv
tomo plugins enable vehicle_cctv
```

Agents use `plugin_manager(action="sync_dependencies", id="vehicle_cctv")`; the
administrator API is `POST /api/plugins/<id>/sync-dependencies`. Enabled source
updates sync new requirements automatically, preserving the old plugin on
failure. Disabled source updates remain disabled and sync later when requested.
