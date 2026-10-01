# Memory lifecycle

All durable facts and uploaded references live in the account's Markdown vault:
`$TOMO_HOME/memory/vault/<user-id>/`. Markdown is authoritative; SQLite contains
rebuildable search and graph indexes. Storage has no total character quota.

## Capture and recall

Use `memory` proactively for durable facts. Pass `entity=type/slug` and one
concise declarative fact per add. Supported types are user, agent, person,
project, tool, place, org, topic.

| Content | Page/tool |
|---|---|
| User identity and preferences | `user/profile` |
| Agent lessons | Agent notes page identified in live context |
| Workplace conventions | Project notes page identified in live context |
| Facts about a person, project, service, place, organization or topic | Corresponding `type/slug` |
| Previous conversations | `session_search` |
| Concrete experiences | `record_episode` / `recall_episodes` |
| Reusable procedures | `manage_skill` |
| Reports and other session files | `save_artifact` / `list_artifacts` |
| Structured state | `agent_state` |

```json
{"action":"add","entity":"project/wazzapi","content":"Uses [[tool/wuzapi]] as its WhatsApp backend.","aliases":["WA backend"]}
{"action":"list"}
{"action":"list","entity":"project/wazzapi"}
{"action":"search","query":"wazzapi backend"}
{"action":"replace","entity":"tool/server","old":"port 8000","content":"Server listens on port 9000."}
{"action":"remove","entity":"tool/server","old":"port 9000"}
```

Replace/remove require a unique substring of a live fact. Supersession requires
an exact existing fact. The retired fact remains marked as superseded within the
vault page for audit history and is excluded from injected context. User identity
belongs on `user/profile`, never `person/me` or a person page for the account ID.
Facts about another person belong on that person's page, with the relationship
stated explicitly. Wikilinks and aliases improve retrieval.

For recall of sessions, use `session_search(query="deployment wazzapi")`.
For saved facts, use `memory(action="search", query="wazzapi")`. Session search
reads account-scoped messages and summaries; memory search reads vault pages.

## UI and API

The Memory page supports page inspection, graph and timeline navigation,
correction, moving and forgetting facts. System → Memory saves facts and uploads
references directly to a vault page. Authenticated routes:

| Route | Purpose |
|---|---|
| `POST /api/memory/facts` | Add `{entity, content}` |
| `POST /api/memory/upload` | Multipart `entity` and `file`; up to 20 MB |
| `GET /api/memory/overview` | Account pages and live facts |
| `GET /api/memory/graph` | Graph and day index |
| `GET /api/memory/entity/<type>/<slug>` | Read one page |
| `POST /api/memory/entity/<type>/<slug>/edit` | Correct a fact |
| `POST /api/memory/entity/<type>/<slug>/move` | Move a fact |
| `POST /api/memory/entity/<type>/<slug>/forget` | Retire a fact |

Every route takes the account from authentication, never a client-supplied owner.

## Context and learning

Profiles, working notes, world cards, and query matches refresh each turn from
vault pages. Excerpts have prompt budgets; those budgets do not restrict storage.
Background review uses `memory` for facts and `manage_skill` for procedures.
`learning_enabled` controls review. `memory_vault_enabled` controls automatic
turn timeline recording and extraction; explicit memory writes and retrieval
always use the vault. Extraction model and nightly consolidation remain
configurable with `memory_extraction_profile_id`, `memory_consolidation_enabled`,
and `memory_consolidation_cron`.

## Migration and backup

Startup performs a one-way migration of old profile, agent, workplace, and
reference files and SQLite knowledge rows. Each source is removed only after its
content is verified in the vault. Retry after an interrupted migration is safe.
Legacy shared files retain the web account's scope; per-account files retain
their owner. Shared workplace notes are copied to web and known participating
accounts. Knowledge metadata is preserved in page frontmatter. The old KB tables,
tools, endpoints and CLI resource are removed; there is no fallback storage.

Back up the entire `$TOMO_HOME` and SQLite database while writes are stopped.
The vault contains facts; the database still contains sessions, summaries,
experiences, configuration and the learning ledger. Vault indexes can be rebuilt
from Markdown on subsequent reads. After restore, verify account isolation,
profile context, search, and a known session through `session_search`.

Implementation: [vault](../../../../app/runtime/memory/vault/),
[memory tool](../../../../app/runtime/tools/memory.py),
[session search](../../../../app/runtime/tools/session_search.py),
[context](../../../../app/runtime/agent/context.py).
