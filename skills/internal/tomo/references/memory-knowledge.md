# Memory, knowledge, learning, and recall

Use this for remembering user preferences/facts, retrieving prior context,
correcting stored information, configuring memory jobs, or creating reusable
lessons. Read the actual enabled tool schema before calling a memory operation.

## Choose the right store

| Information | Appropriate path |
| --- | --- |
| Who the user is, communication preferences, corrections about how to work | `memory` target `user` |
| Facts about a project, person other than the user, service/tool, place, organization, topic | `memory` target `entity`, with `<type>/<slug>` |
| Agent working conventions or durable tool lessons | `memory` target `memory` |
| Conventions for the current repository/workplace | `memory` target `project` |
| Searchable titled note with tags/body | `remember` / knowledge entry; retrieve with `recall` |
| Reusable procedure for a class of tasks | Library skill via `manage_skill` |
| A coherent experience, including failure/outcome/reflection | `record_episode` |
| What happened in a past chat, task progress, PR/commit discussion | `session_search`; do not turn every event into durable memory |

The user's own profile belongs in the user target, not a `person/me` vault page.
Entity types are `person`, `project`, `tool`, `place`, `org`, and `topic`; use a
stable lowercase-hyphen slug and related `[[type/slug]]` links where useful.
One concise fact per addition makes later corrections easier. Record observed
facts and provenance; do not persist unverified guesses as established truth.

## Recall before answering or writing

Use current linked/context memory first, then the relevant enabled retrieval
path. For “what do you know about my project/server/preferences”, search its
entity/user notes rather than claiming no knowledge without checking. Inspect
existing entries before adding duplicates or replacing a changed attribute.

```text
memory(action="list", target="entity")
memory(action="list", target="entity", entity="project/tomo")
recall(query="Tomo deployment conventions", limit=5)
```

`recall` searches the knowledge base; it is not a complete session-history or
entity-vault listing. `session_search` searches chat messages, while episodes
capture experience. Tools can be disabled; use available capabilities and state
which source was inspected instead of inventing a successful retrieval.

## Add and correct deliberately

For curated notes, `memory` supports `add`, `replace`, `remove`, and `list`;
entity targets support `add`/`list`, with `supersedes` for an exact prior entity
fact when an attribute changes. Other replace/remove operations require a short
unique `old` substring. Read the actual target before replacing content. Keep
unrelated facts and user ownership intact.

`remember(title=..., body=..., tags=[...])` stores a searchable knowledge note.
Do not put tokens, passwords, raw private dumps, temporary TODOs, or completed-work
logs in general memory. Summarize durable facts/lessons and use the appropriate
session/artifact storage for task evidence.

## Configure knowledge through CLI

```bash
tomo config knowledge schema --json
tomo config knowledge list --json
tomo config knowledge create --set title='Deployment convention' --set body='Use the existing coordinator installation.' --set tags='["deployment"]' --json
tomo config knowledge update <entry-id> --set body='The corrected verified fact.' --json
```

CLI knowledge follows the model's user scope; a CLI listing is not a global
inventory of every account's notes. Runtime operations inherit user/session
context. Resolve ownership before editing/deleting and do not copy private
knowledge across users to make it easier to retrieve. Read back title/body/tags
and verify recall when retrieval is part of the task. Lack of semantic embeddings
does not mean the entry vanished; lexical lookup may still work.

## Learning and consolidation

Read [channels/settings](channels-settings.md) for `learning_enabled`, review
profile, cooldown/nudge values, vault enablement, extraction profile, and
consolidation cron. Profiles must actually exist and be usable. Learning review
runs on eligible activity; enabling it does not guarantee a specific new skill.
Inspect the resulting library file or knowledge change before reporting success.

Consolidation is a runtime job, not a manual memory edit. Apply job-setting changes
through the coordinator lifecycle when needed, then verify logs/resulting state.
Do not automatically rewrite broad personas or clear existing memory to fix one
incorrect fact. Preserve the master encryption key and established Home paths
from [config](config.md).
