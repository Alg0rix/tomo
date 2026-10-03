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
through `api.router`; do not modify global Tomo routers. A `static/` directory is served at `/plugins/<id>/static/` and follows plugin
enablement. Register `api.on_turn_end(callback)` to receive a `TurnEndContext`
with session, agent, message, and token counts. WebSockets are not supported in this version.
Tools have ids `plugin__<id>__<name>` and synchronous handlers returning strings
or JSON-serializable values. Validate tool arguments in the handler.

`api.user_data_dir()` returns a current-agent-user data directory. For HTTP
handlers use `api.user_data_dir(session_user_id(request))`. The Money example
stores amounts as integer minor units and uses the same ledger in both its pages
and agent tools. Agents use Tomo's existing models and conversation; the plugin
needs no separate AI key or agent loop.

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
