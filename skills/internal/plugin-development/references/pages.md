# Pages, navigation, and design

`api.router` is a FastAPI APIRouter mounted below `/plugins/<id>`. Register each
navigation page using `api.page('/relative-route', 'Label')`; the first page
appears in the sidebar under the plugin's name. Registered pages are navigation
metadata; each still needs an actual route. Links use `api.base_url` or
`plugin.base_url` in rendered templates, including forms and asset URLs.

`api.render(request, 'template.html', **context)` loads the plugin's `templates/`
first and Tomo's templates second. Extend `base.html`; it supplies the rail,
theme, global stylesheet, and Tomo browser helpers. Reuse `page`, `page-head`,
`btn`, `input`, and theme tokens `--text`, `--text-dim`, `--surface`, `--border`,
and `--accent`. Scope plugin CSS to a page class. Keep URLs relative and verify
390px mobile and desktop widths, keyboard interaction, and empty/error states.

A `static/` directory is automatically served at `api.base_url + '/static'`
while enabled. Link assets through that base URL. Assets and pages become
unavailable when disabled. Authentication is enforced by the runtime for routes
and assets; handlers still enforce their own ownership and mutation rules.

Use a named Lucide icon in the manifest. Supported names: `puzzle`, `wallet`,
`chart-column`, `columns-3`, `library`, `notebook`, `calendar`, `globe`, `code`,
`briefcase`, `heart`, `layout-grid`. The catalog listing should use the same icon.
Tomo renders it locally in cards, details, System, and sidebar navigation.

For mutation forms/API routes, validate inputs server-side. Reject cross-origin
browser mutations; do not rely solely on a client-side confirmation. Jinja
escapes data by default; do not apply `safe` to user/model text. Use bounded
responses and make errors useful without exposing server secrets or traces.
