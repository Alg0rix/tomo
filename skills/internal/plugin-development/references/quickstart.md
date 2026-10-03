# First plugin

Use a separate directory or repository. For official work use `tomo-plugins`;
for user work use the selected local workplace or agent work directory. Do not
add a feature package to `app/` or an `examples/plugins` directory in core.

A minimal layout is `tomo-plugin.json`, `plugin.py`, `templates/overview.html`,
dependency declarations in `requirements.txt` or `pyproject.toml` when needed,
and optionally `static/` and `skills/<skill-name>/SKILL.md`.

```json
{"id":"my_plugin","name":"My plugin","description":"What it does","version":"0.1.0","sdk_version":1,"icon":"notebook"}
```

IDs use lowercase letters, numbers, and underscores, begin with a letter, and
fit 32 characters. Use a stable ID: reload cannot change it. Icon is an optional
named Lucide icon; default is `puzzle`. No raw SVG or external image URLs.

```python
from fastapi import Request

def setup(api):
    api.page("/", "Overview")

    @api.router.get("/")
    def overview(request: Request):
        return api.render(request, "overview.html")
```

```jinja2
{% extends "base.html" %}
{% block content %}
<div class="page"><h1 class="page-title">My plugin</h1></div>
{% endblock %}
```

Install with `plugin_manager(action="install", path="/server/path/my-plugin")`,
then enable by the manifest ID when authorized. Installation registers the
source, does not execute it, and leaves it disabled. Your source folder must
stay available. After edits call Reload, not another Install or a server restart.

Before Enable, use plugin_manager sync_dependencies if required packages are
missing. Declare distribution names rather than guessing from Python imports.
