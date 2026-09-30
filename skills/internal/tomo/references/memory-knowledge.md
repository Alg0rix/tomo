# Memory lifecycle: capture, link, recall, correct, and forget

Use this to operate Tomo memory from first capture through reuse, correction,
consolidation, and retirement. Read enabled tool schemas before calling tools;
examples below are tool arguments, not Python or CLI functions. Run local
configuration on the coordinator with its actual Home/database; see
[CLI configuration](configuration-cli.md).

In Tomo, load this whole reference with
`use_skill(skill_id="tomo", file="references/memory-knowledge.md", limit=40000)`.
The default page is 12,000 characters; follow the returned `offset` continuation
when using default pagination so correction/retention guidance is not missed.

Contents:

- [Choose storage and scope](#choose-storage-and-scope)
- [Inspect and capture](#inspect-and-capture)
- [Link entities and add aliases](#link-entities-and-add-aliases)
- [Recall and reuse](#recall-and-reuse)
- [Correct, move, and unlink](#correct-move-and-unlink)
- [Forget and retention](#forget-and-retention)
- [Knowledge and episodes](#knowledge-and-episodes)
- [Automatic capture and consolidation](#automatic-capture-and-consolidation)
- [Inspect files, back up, and restore](#inspect-files-back-up-and-restore)
- [Verification and troubleshooting](#verification-and-troubleshooting)

## Choose storage and scope

| Information | Write / retrieve | Scope and authoritative storage |
| --- | --- | --- |
| User's own identity, role, communication preferences, corrections to how you work | `memory`, target `user` | Account; `$TOMO_HOME/memories/users/<user-id>/USER.md` |
| Facts about a project, another person, service/tool, place, organization, topic | `memory`, target `entity`, key `<type>/<slug>` | Account; `$TOMO_HOME/memory/vault/<user-id>/entities/<type>/<slug>.md` |
| Agent working notes, tool quirks, durable lessons for that agent | `memory`, target `memory` | Account + agent; `$TOMO_HOME/agents/<agent-id>/users/<user-id>/MEMORY.md` |
| Repository/workplace architecture, layout, conventions | `memory`, target `project` | Workplace; `$TOMO_HOME/workplaces/<workplace-id>/PROJECT.md` |
| Longer searchable titled note, supporting explanation, tags | `remember` / `recall` | Account; SQLite knowledge entries |
| Concrete experience: objective, attempts, outcome, reflection | `record_episode` / `recall_episodes` | Account, with agent/session/workplace context; SQLite episodes |
| Reusable procedure for a class of tasks | `manage_skill` / `use_skill` | Library skill files; see [skills and modules](skills-modules.md) |
| Past conversations, task progress, PR/commit discussion | `session_search` | Account's session history, not a curated fact |
| End-of-turn day notes, source trail | Automatic vault recording / Memory journal | Account; `$TOMO_HOME/memory/vault/<user-id>/timeline/YYYY/MM/YYYY-MM-DD.md` |

Route each durable fact to one primary store. Entity `project/tomo` describes
that project in the user's world; target `project` describes the bound workplace.
They are different scopes. `PROJECT.md` lives on the coordinator under the
workplace ID, not automatically in the remote repository, and is not isolated
per login like user/entity memory.

The user's profile belongs in target `user`, not `person/me` or
`person/<user-id>`. A preference about a specific thing can live on that thing's
page: `person/max-verstappen` → “The user's favorite F1 driver.” Runtime tools
inherit the current account; do not invent a `user_id` memory argument or copy
another account's private notes into this account to make retrieval work.

## Inspect and capture

1. Check current context and the relevant store before adding or correcting.
2. Keep one concise, declarative fact per addition, understandable in a future
   session without this conversation.
3. Save newly observed durable facts proactively; do not require “remember
   this” for every stable preference or correction. Preserve useful provenance;
   do not persist unverified guesses as established truth.
4. Read back the stored result. A response alone does not prove a correction
   matched or that a second fact was added.

```text
memory(action="list", target="user")
memory(action="list", target="memory")
memory(action="list", target="entity")
memory(action="list", target="entity", entity="project/tomo")
memory(action="list", target="project", workplace_id="<actual-workplace-id>")

memory(action="add", target="user",
       content="User prefers concise Indonesian replies.")
memory(action="add", target="memory",
       content="The example news site requires a User-Agent header for web_fetch.")
memory(action="add", target="project", workplace_id="<actual-workplace-id>",
       content="The workplace uses FastAPI and vanilla JavaScript; templates are under app/templates.")
memory(action="add", target="entity", entity="project/tomo",
       content="Tomo is an agent platform built with FastAPI and vanilla JavaScript.",
       aliases=["Tomo", "tomo-app"])
```

Omitted `target` defaults to `memory`, so specify it explicitly. `agent_id`
defaults to the bound agent. Project `workplace_id` defaults from that agent's
configured workplace, not necessarily a per-session workplace override; pass the
actual ID when targeting a specific workplace.

| Target | Supported `memory` actions | Important detail |
| --- | --- | --- |
| `user`, `memory` | `list`, `add`, `replace`, `remove` | `old` must identify exactly one entry |
| `entity` | `list`, `add` | Correct with `add` + exact `supersedes`; UI/API can edit/move/forget |
| `project` | `list`, `add` | No tool `replace`/`remove` support |
| `all` | `list` | User, agent notes, and entity index; excludes project, KB, episodes, history |

Entity index output previews at most 40 pages and three live facts per page.
Curated listings also truncate previews; read the actual page/file for exact
text. Neither listing is a complete full-text export.

Skip secrets, credentials, raw private dumps, trivial facts, temporary TODOs,
routine completed-work logs, issue/PR numbers, and commit SHAs. Use session/
artifact storage for task evidence. Record a substantial experience as an episode
when its attempts and outcome could inform future decisions.

## Link entities and add aliases

An entity key is `<type>/<slug>`. Types: `person`, `project`, `tool`, `place`,
`org`, `topic`. Prefer stable lowercase-hyphen slugs (`tool/aio-serv`); the parser
also accepts lowercase digits/underscores, with a maximum slug length of 128.
Put alternate names and abbreviations in `aliases`, not duplicate pages.

Create a relationship by placing `[[type/slug]]` inside a fact on the source
page. There is no separate `link_memory` tool or link action.

```text
memory(action="add", target="entity", entity="tool/aio-serv",
       content="aio-serv is the user's deployment server.",
       aliases=["aio serv", "deployment server"])
memory(action="add", target="entity", entity="org/example-studio",
       content="Example Studio maintains [[project/tomo]].")
memory(action="add", target="entity", entity="project/tomo",
       content="Tomo is deployed on [[tool/aio-serv]] and maintained by [[org/example-studio]].")
memory(action="list", target="entity", entity="project/tomo")
```

- A link points from the fact's page to the referenced entity. Memory UI shows
  “Links to” and “Linked from”; retrieval can traverse outgoing links and
  backlinks. A reverse fact is only needed for additional information.
- A link does not create the destination page. Save a verified fact about that
  entity or reuse its existing page. Missing targets remain unresolved until
  indexed; do not fabricate placeholder facts merely to connect the graph.
- Canonical `[[tool/aio-serv]]` is preferred. Slugs/titles/aliases can resolve
  case-insensitively, but a shared alias can resolve to the first matching page.
  Aliases help search/discovery and do not rename the entity key. `add` merges
  aliases, including when the submitted fact is a duplicate.
- Use plain `[[type/slug]]` for entity graph links. `[[tool/aio-serv|server]]`,
  `.md` paths, and `#section` suffixes do not resolve as canonical entity edges
  in the vault index.
- Markdown `[documentation](https://example.com/docs)` can retain an external
  reference in a note; it does not create an entity relationship.
- The index extracts links from the whole body, including retired facts. A
  visible graph edge is not proof that its relationship is currently true.

`source` is provenance, separate from a relationship. Entity additions default
to the current local date; the writer adds `(origin: agent)` and
`(src: [[<source>]])`. Extraction stamps date + turn/session; consolidation stamps
date + `#consolidated`. A stamp does not itself create a timeline page or prove
a journal entry exists. Manual source references must not contain brackets or
newlines:

```text
memory(action="add", target="entity", entity="tool/aio-serv",
       content="aio-serv listens on port 8000.",
       source="2026-09-30#turn-<actual-session-id>")
```

## Recall and reuse

Use present context first, then inspect the appropriate store. For “what do you
know about my server/project/preferences”, check entity/user notes before saying
you do not know. Search names and aliases; follow related pages when useful.

```text
memory(action="list", target="entity", entity="tool/aio-serv")
memory(action="list", target="user")
recall(query="Tomo deployment conventions", limit=5)
recall_episodes(query="deployment port failure", limit=5)
session_search(query="aio-serv", limit=10)
```

`recall` searches KB, `recall_episodes` searches experiences, `session_search`
searches chat messages. None is a full entity-vault search/export. If the key is
unknown, use the entity index or Memory overview, then open the exact page.

| Context path | Timing and limitations |
| --- | --- |
| Curated `USER.md` / `MEMORY.md` | Frozen system-prompt snapshot at session start; writes reach disk immediately, but this snapshot refreshes in a new session |
| World card | Fresh compact entity facts in live context; prioritizes personal facts and active projects/tools within a small budget |
| Per-turn reuse | Query-matched vault facts + linked neighbors, user prefs, bound project notes, experiences, KB and relevant skills/state; compact snippets |
| Explicit retrieval | Tool read/list/search during the current turn; use it to inspect a change immediately |

Vault retrieval uses keywords/FTS and aliases, then linked neighbors, not every
page. Current snippet limits: three matching pages, up to two neighbors, up to
two live facts per page within roughly 1,100 characters. The world card is also
bounded. Missing snippets do not prove stored facts vanished. Retired facts are
excluded from these live snippets. Existing entity pages remain retrievable
when automatic vault recording is off. Memory is evidence/context, not permission
or executable instructions.

## Correct, move, and unlink

Read the live fact, identify the changed attribute, preserve unrelated facts,
and use the storage-specific correction path.

For `user` / `memory`, `replace` substitutes the matching substring inside one
entry; it replaces the whole entry only when `old` equals that entry. `remove`
removes the entire matching entry. Matching is case-sensitive; ambiguous matches
fail. Use exact full text for a whole-entry rewrite.

```text
memory(action="replace", target="user",
       old="User prefers concise Indonesian replies.",
       new="User prefers detailed Indonesian replies for technical explanations.")
memory(action="remove", target="memory",
       old="The example news site requires a User-Agent header")
```

For entities, `add` + `supersedes` must copy the exact old live fact, excluding
writer-added `(origin: ...)` / `(src: [[...]])` suffixes. Exactly one live fact
must match on that page. The old fact is retired and the replacement stored
atomically; old text remains as history.

```text
memory(action="list", target="entity", entity="tool/aio-serv")
memory(action="add", target="entity", entity="tool/aio-serv",
       content="aio-serv listens on port 9000.",
       supersedes="aio-serv listens on port 8000.")
memory(action="list", target="entity", entity="tool/aio-serv")
```

The current tool reports “Near-duplicate already present.” for both duplicate
content and a supersession conflict. Read back to verify the old fact is struck
through and the new one live. Do not retry with approximate `supersedes` text.

To add/remove a relationship on an existing fact, supersede it with text containing
the intended links. A removed link can remain as a graph edge in retained
history. `supersedes` cannot move a fact between pages or rename a page.

Memory UI supports edit/move/forget for individual entity facts. The authenticated
API supports those operations under the current account:

| Operation | Request |
| --- | --- |
| Read raw page and complete fact array | `GET /api/memory/entity/<type>/<slug>` |
| Edit fact | `POST /api/memory/entity/<type>/<slug>/edit`, body `{"number":0,"text":"Corrected fact.","expected":"Exact old fact."}` |
| Move fact to existing/new page | `POST /api/memory/entity/<type>/<slug>/move`, body `{"number":0,"destination":"project/tomo","expected":"Exact old fact."}` |
| Retire fact | `POST /api/memory/entity/<type>/<slug>/forget`, body `{"number":0}` |

`number` is the zero-based position in the complete fact array, including retired
entries, not the visible live-list position. Overview returns it as `facts[].n`.
`expected` is decoded text without provenance; include it for edit/move to avoid
overwriting a concurrent correction. HTTP 409 means refresh and reassess. Forget
has no `expected` guard: fetch current state immediately before targeting its
number. Move writes the destination with user provenance and retires the source;
it does not rename pages or rewrite incoming links. Use existing authenticated
access; CLI configuration is a separate execution path.

Project memory has no tool correction/removal. For authorized coordinator file
maintenance, resolve the exact `PROJECT.md` from the target listing, back it up,
edit only the intended `§`-delimited entry, preserve other entries, and read back.
Do not append a contradictory fact or pretend target `project` supports replace.

## Forget and retention

| Store | Removal path | What remains |
| --- | --- | --- |
| User / agent notes | `memory(action="remove", target=..., old=...)` | Entry leaves that file; existing prompt snapshots/chat/backups may still contain it |
| Entity fact | Memory UI / entity `/forget` API | Struck-through fact retained as superseded history, excluded from live retrieval |
| KB entry | `forget_memory(id="<actual-entry-id>")` or CLI knowledge delete | KB entry deleted; does not remove related entity/episode/chat |
| Project notes | Scoped coordinator file edit | Tool has no remove action; preserve unrelated entries |
| Episode | No `forget_memory` support | Archiving/supersession changes lifecycle; do not invent an episode delete tool |

`forget_memory(query=...)` deletes the top KB match without a selection step;
prefer a verified ID. `recall` shows titles/bodies but currently not IDs; obtain
the ID from `remember`'s result or KB list/show before deleting.

Entity forget is retirement, not permanent erasure. UI exposes forgotten facts
and raw Markdown; historical links and source journal/chat may remain. Background
extraction/consolidation refuse an exact re-add of retired text, but this does
not guarantee protection from paraphrases or explicit manual re-add. For a
permanent-erasure request, inspect actual copies across files, DB stores, source
history, snapshots, and backups within the authorized scope; report remaining
retention. Memory tools do not implement one-command account-wide erasure.

## Knowledge and episodes

Use KB for longer searchable notes, with clear titles and useful tags:

```text
remember(title="Tomo deployment convention",
         body="Deploy through the existing coordinator service. Verify its data root before updating. Source: the deployment runbook.",
         tags=["tomo", "deployment"])
recall(query="Tomo deployment convention", limit=5)
forget_memory(id="<verified-knowledge-id>")
```

Knowledge has CLI CRUD; entities and episodes have no equivalent
`tomo config memory` commands:

```bash
tomo config knowledge schema --json
tomo config knowledge list --json
tomo config knowledge show <actual-entry-id> --json
tomo config knowledge create --set title='Deployment convention' --set body='Use the existing coordinator installation.' --set tags='["deployment"]' --json
tomo config knowledge update <actual-entry-id> --set body='The corrected verified fact.' --json
tomo config knowledge delete <actual-entry-id> --json
```

CLI knowledge uses the model's current/default user scope (normally `web` outside
a bound turn), not all accounts. Verify ownership; use the owning account's
runtime/authenticated API when needed. Search before creating duplicates and
reuse the ID for correction. Recall uses lexical FTS with optional semantic
embeddings; lack of embeddings does not mean an entry vanished. Wikilinks in KB
text do not create vault graph edges.

Record a coherent experience, including failures and what changed the outcome:

```text
record_episode(title="Deployment recovered after port correction",
               objective="Restore the deployment endpoint",
               context_summary="Tomo on aio-serv; expected port 8000",
               trajectory_summary="The old port failed. Checked service configuration and verified port 9000.",
               outcome_status="success",
               outcome_summary="The endpoint responded on the verified port.",
               reflection_summary="Check live service configuration before reusing an old port assumption.",
               what_failed=["Connecting to the old port"],
               what_worked=["Inspecting live configuration"])
recall_episodes(query="deployment port correction", limit=5)
```

The tool binds account/agent/session and available workplace context. `content`
is a narrative fallback; at least one experience field must be nonempty. Save
the returned ID. `parent_episode_id` can connect a child to a known parent;
episode relations are a separate experience graph, not entity wikilinks. Similar
experiences can be linked automatically.

States include `candidate`, `validated`, `active`, `consolidated`, `superseded`,
`archived`; not every episode passes through every state. Search favors usable
experiences and excludes retired ones. Authenticated episode API supports
list/search/detail, session open/close, helpful feedback, contradiction inspection,
and optimization; read its actual contract before calling it. Optimization can
decay/archive weak unused experiences and distill lessons/procedures into scoped
KB notes with source episode IDs. A `procedural` KB tag is not an installed
library skill; deliberately create playbooks through `manage_skill`.

## Automatic capture and consolidation

| Control | Actual behavior |
| --- | --- |
| Explicit memory / KB / episode tools | Can save during a turn when enabled for the agent; background learning is not required |
| `memory_vault_enabled` | End-of-turn goal/outcome summaries in the account's timeline + background entity extraction |
| `memory_extraction_profile_id` | Extraction profile ID; otherwise tries learning review profile, then an enabled small-model profile, then normal LLM fallback |
| `learning_enabled` | Eligible background review may update curated notes, KB, skills, episodes; separate from vault extraction |
| `memory_consolidation_enabled` + `memory_consolidation_cron` | Scheduled prior-day vault consolidation + episode long-term optimization |

Vault recording is a compact source trail, not a full transcript. Extraction
requests zero to three durable facts, reuses entity keys/aliases, and uses exact
supersession for changes. It runs in the background per account; the next turn
waits for pending extraction. Manual edits made while extraction runs take
precedence over the stale snapshot. Journal presence alone does not prove
extraction succeeded.

Consolidation processes prior unconsolidated days, preserves newer facts, and
marks processed pages `consolidated: true`; a day with no durable facts can still
be processed. Today is skipped. Appending to a day resets its flag to false.
Nightly vault consolidation uses the default LLM, not extraction profile
selection, and also runs episode optimization for each account. Inspect live
facts/logs rather than treating job counts as proof of correct additions.

```bash
tomo config settings show --json
tomo config llm-profiles list --json
tomo config settings update --set memory_vault_enabled=true --set memory_extraction_profile_id=<actual-enabled-profile-id> --json
tomo config settings update --set memory_consolidation_enabled=true --set memory_consolidation_cron='0 3 * * *' --json
```

These examples apply to an authorized settings change, not prerequisites for
manual memory use. Cron has five fields and uses the scheduler's configured
timezone, falling back to the host timezone. Check local dates/day boundaries.
CLI saves do not reschedule the running server job; apply the coordinator
lifecycle when required. API settings updates can refresh the runtime job.
See [channels and settings](channels-settings.md) for learning cooldown/nudges
and profile settings. Enabling review does not guarantee a new skill or
retroactive extraction of old chats.

## Inspect files, back up, and restore

Vault Markdown is authoritative; SQLite `vault_docs`, `vault_aliases`,
`vault_links`, `vault_fts` are derived indexes. Writes reindex the page; normal
vault retrieval/overview rebuilds changed/missing documents. Generated vault
`index.md` is a page directory, not a file to edit manually. Do not invent
`tomo memory reindex`.

Entity files contain limited frontmatter (`type`, `aliases`, `tags`, `updated`),
a heading, and facts beginning with `§ `. Retired facts use
`~~...~~ superseded <date>`. The codec is not a general YAML parser; prefer
tools/UI/API over hand-editing frontmatter/provenance. Facts must be nonempty
and at most 2,000 characters. Curated budgets: USER 2,000, agent MEMORY 4,000,
PROJECT 4,000 characters. Near-duplicate additions are suppressed. Compact stale
entries deliberately; longer notes belong in KB rather than raw prompt dumps.

For backup/migration, resolve the actual coordinator Home and DB paths. Preserve
scoped Markdown, library skills, SQLite KB/episodes/history, and the established
encryption key/configuration together. Use a consistent database backup procedure
or stopped-service copy; a live SQLite main-file copy can omit WAL data. A
DB-only backup misses vault/curated files; a vault-only backup misses KB/episodes.
See [config and secrets](config.md) and [install paths](install.md).

Restore into the intended installation with the same account/agent/workplace
identity mapping. Verify scoped files and KB/episodes; use a normal vault read
to rebuild derived indexes, then a new session to verify the curated snapshot.
Legacy `memories/USER.md` and `agents/<id>/MEMORY.md` can be fallback reads for
`web`; they are not normal per-account write destinations.

## Verification and troubleshooting

Authenticated `/memory` exposes entities, live/forgotten facts, aliases, outgoing/
incoming links, raw Markdown, and a paged journal. Programmatic read paths:

| Read | Purpose |
| --- | --- |
| `GET /api/memory/overview` | Complete account entity/fact overview, resolved links, activity counts |
| `GET /api/memory/graph` | Entity nodes/edges; counts and edges can include historical facts |
| `GET /api/memory/journal?days=10` | Paged notes; returned `next` becomes the next request's `before` |
| `GET /api/memory/journal?entity=project/tomo` | Turns with resolved wikilinks; plain-text mentions alone may not match |
| `GET /api/memory/journal?q=deployment&pending=true` | Keyword-filtered unconsolidated days |
| `GET /api/memory/timeline?date=2026-09-30` | One day's raw Markdown and note blocks |

Verify the requested operation in its store: capture → live fact present;
link → intended destination and resolved edge; correction → replacement live,
old entity fact retired; move → destination live/source retired; forget → absent
from live retrieval with retention explained; consolidation → processed day plus
actual resulting state. Distinguish immediate tool reads, later-turn live
context, and a new session's curated snapshot.

| Symptom | Check / next action |
| --- | --- |
| Saved, but chat still recalls old value | Read storage; curated snapshot is frozen, live retrieval is bounded; use a new session for snapshot verification |
| Missing entity link | Correct type/slug, destination under this account, supported syntax, unambiguous aliases; overview rebuilds index |
| Page missing from tool index | Previews stop at 40 pages; request exact key or authenticated overview |
| Correction reports duplicate | Exact live `supersedes` without provenance, correct page/account, read-back state; response also masks conflicts |
| Replace/remove fails | Supported target, unique case-sensitive substring, budget; entity/project do not support these actions |
| No recalled knowledge | Correct store/account and keywords; `recall` does not search entities or all history |
| Journal but no extracted fact | Background extraction result, profile availability, logs, and durable evidence in the turn |
| Consolidation did nothing | Enabled setting, live cron/timezone, prior pending day, default LLM usability; today is skipped |
| Forget leaves page/link/history | Retirement retains Markdown/historical links; verify live facts, not graph presence |
| Memory at limit | Remove/replace stale entries where supported, compact or move longer notes to KB, preserve unrelated facts |

Implementation anchors in a source checkout for checking a changed version:
[memory tool](../../../../app/runtime/tools/memory.py),
[vault write/correction](../../../../app/runtime/memory/vault/write.py),
[link index](../../../../app/runtime/memory/vault/index.py),
[vault retrieval](../../../../app/runtime/memory/vault/read.py),
[per-turn reuse](../../../../app/runtime/memory/retrieve.py),
[curated notes](../../../../app/runtime/memory/curated.py),
[extraction](../../../../app/runtime/memory/vault/extract.py),
[consolidation](../../../../app/runtime/memory/vault/consolidate.py),
[episodes](../../../../app/runtime/memory/episodes.py), and
[Memory API](../../../../app/api/rest.py).
