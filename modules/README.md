# Tomo modules

Optional product surfaces that extend Tomo (usage analytics, boards, …).
Each module is a Python package under this directory (Django-style layout).

## Layout

```text
modules/
  README.md                 ← you are here
  __init__.py               ← public exports
  base.py                   ← ModuleMeta, TurnEndContext, Module protocol
  registry.py               ← discovery, SQLite sync, turn-end dispatch, routes
  paths.py                  ← templates/static path helpers
  <module_id>/
    __init__.py             ← re-export `module` (+ META) for the registry
    modules.py              ← ModuleMeta, class, hooks (like Django apps.py)
    routes.py               ← register_api / register_pages (like urls.py)
    templates/              ← Jinja pages (`page.html` → name `<id>/page.html`)
    static/                 ← CSS/JS served at `/m/<id>/static/…`
    …                       ← ledger, models helpers as needed
```

Built-ins today:

| id | Package | UI |
|----|---------|-----|
| `token_monitor` | `modules/token_monitor/` | `/usage` |
| `kanban` | `modules/kanban/` | `/board` (stub) |

**Not modules:** Tomo Connector / tunnel workplaces are **first-class** core
features (Workplaces UI + `/api/connector/*`), not catalog entries.

## Add a module (checklist)

1. **Create the package** with Django-style files:

```text
modules/<module_id>/
  __init__.py
  modules.py
  routes.py
  templates/          # if has_ui
  static/             # if has_ui
```

2. **`modules.py` — metadata + hooks:**

```python
from modules.base import ModuleMeta, TurnEndContext

META = ModuleMeta(
    id="my_module",                 # stable id (snake_case)
    name="My Module",
    description="What it does",
    version="0.1",
    has_ui=True,
    ui_path="/my-module",
    nav_label="My Module",          # optional top-nav link (empty = gallery only)
    default_enabled=True,
)

class MyModule:
    meta = META

    def on_turn_end(self, ctx: TurnEndContext) -> None:
        ...

    def register_routes(self, api_router) -> None:
        from .routes import register_api
        register_api(api_router)

    def register_pages(self, web_router) -> None:
        from .routes import register_pages
        register_pages(web_router)

module = MyModule()
```

3. **`routes.py` — HTTP only:**

```python
def register_api(api_router) -> None:
    # Gate with store.is_module_enabled("my_module")
    ...

def register_pages(web_router) -> None:
    # Templates: modules/my_module/templates/
    # Static: /m/my_module/static/
    ...
```

4. **`__init__.py` — thin export:**

```python
from .modules import META, module
__all__ = ["META", "module"]
```

5. **Register discovery** — append `"modules.my_module"` to `_BUILTIN` in
   `modules/registry.py`.

6. **SQLite catalog** — on next DB migrate / seed, `sync_module_rows` inserts a
   `modules` row for new ids (does not overwrite existing enable flags).

7. **UI (if `has_ui`)** — assets stay in the module package:

   - Template: `modules/<id>/templates/page.html` (Jinja name `<id>/page.html`)
   - CSS/JS: `modules/<id>/static/` via `{{ module_static('<id>', 'page.css') }}`
   - Pages: `register_pages` (redirect to `/modules` when disabled)

8. **Tests** — enable/disable the module, hit the page/API, assert hooks fire only
   when enabled.

## Enable / disable

Operators toggle modules in **System → Modules** or **`/modules`**.
Disabled modules:

- skip `on_turn_end`
- should 404 / redirect their UI and APIs

## Conventions

- **Ids are stable** — renaming an id orphans the SQLite row; prefer new id + migration.
- **Do not commit secrets** into module code; use Tomo settings / Fernet secrets.
- **Keep core thin** — module-specific tables (e.g. `usage_events`) live with the
  module’s docs/comments; DDL still ships via `app/models/schema.py` for Alpha.
- **UI stays with the module** — do not add module pages under `app/templates/`.
- **Split like Django** — `modules.py` (definition/hooks), `routes.py` (HTTP),
  thin `__init__.py`. Never import `app.web` / `app.services` at the top of
  `modules.py` / `__init__.py` (Store seed loads the package mid-import).
- **Top nav** — set ``ModuleMeta.nav_label`` (with ``ui_path``) to appear in the
  header when the module is enabled; leave empty for Modules-gallery-only.
- **Hooks must be fast and safe** — never raise into the chat turn; registry logs
  and swallows exceptions.

## Token Monitor reference

See `modules/token_monitor/` for a full example: turn-end ledger writes,
`GET /api/usage`, and the `/usage` contribution heatmap UI
(`templates/page.html` + `static/page.{js,css}`).
