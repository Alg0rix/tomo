# Agent tools and private data

Register tools with `api.tool(name, description, parameters, handler)` during
setup. A handler is synchronous and receives the arguments object. Parameters
must be an object JSON schema. The runtime names the tool
`plugin__<plugin-id>__<name>`; the full name must fit 64 characters. Tool names use
lowercase letters, digits, and underscores. Names are stable across reloads.

```python
def summary(arguments):
    user_dir = api.user_data_dir()
    return {"items": read_items(user_dir)}

api.tool("summary", "Summarize my saved items",
         {"type": "object", "properties": {}, "additionalProperties": False},
         summary)
```

Validate values inside the handler too; JSON schemas are descriptions, not a
replacement for server validation. Distinguish reads from writes in names and
schemas. Return structured, bounded output, not an unbounded database dump.
Do not call an LLM inside setup or wrap Tomo's agents in another AI client.

Tool handlers use `api.user_data_dir()` to resolve the tool's authenticated user.
HTTP handlers must pass `session_user_id(request)` from `app.core.deps` explicitly
as `api.user_data_dir(user_id)`. Never accept another user's ID from arguments,
query parameters, or form fields. For session resources use the ownership checks
from `app.core.deps` rather than trusting a supplied session ID.

Keep persistent state in `api.data_dir` or its per-user directories, not in the
source folder or module globals. Use atomic writes or database transactions.
Reload, disable, and uninstall keep plugin data. For money, use integer minor
units, validate decimal precision, and apply the same ledger operations from
pages and agent tools. See `plugins/money` in the official source repository.

Tools follow normal per-agent selections and permissions. Enabling a plugin does
not grant every agent all its tools. Check `agent_info` or current tool settings;
preserve unrelated selections when editing them. New turns see new tool schemas;
a turn already running retains its initial schema list. Disabled tools refuse
execution even if an old turn still has their schemas.
