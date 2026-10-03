# Vault linking and repair

Markdown under `$TOMO_HOME/memory/vault/<user-id>` is authoritative. SQLite
`vault_docs`, `vault_aliases`, `vault_links` and `vault_fts` are derived indexes.

## Linking rules

- Entity writes infer mentions from indexed aliases, slugs and titles in the same
  account. Names shorter than four characters, generic names, ambiguous aliases,
  self-links and partial-word matches are skipped. Longest overlapping name wins;
  each fact (and alias enrichment) contributes at most five automatic links.
- Automatic links live in a marked `Related:` block **before the first `§`**.
  Fact text/provenance stays exact. Writes, edit and forget recompute that block
  from live facts; manually authored wikilinks are preserved.
- Timeline links resolve their document separately from `#fragment`. Both legacy
  date-only links and scoped `user/timeline/YYYY/MM/YYYY-MM-DD.md#fragment` work.
  Resolution does not assert that a Markdown heading for the fragment exists.
- Cross-account links remain unresolved. Repair reports them; it does not import,
  federate or delete another account's memory.

`scoped_key()` produces `type/readable_identifier-<sha8>`. Agent/project keys
still hash their case-sensitive identifiers. Episodic topics use a stable
experience/procedure title rather than hashing the entire changing fact body.
This groups lessons with the same title, not arbitrary synonymous concepts.

## Repair/migration

The existing startup migration repairs every account directory, including
scheduler accounts not present in the users table. It:

1. Recovers agent/project identifiers from DB records and home directories;
   recovers imported/episodic topic identities where possible. Opaque legacy
   topics use their title/first fact for readability and retain the old hash
   prefix for identity.
2. Writes readable pages, merging exact entries when recovered identities agree.
   Old full keys and bare hash slugs remain frontmatter aliases.
3. Rewrites same-account inbound wikilinks, preserves fragments, and archives old
   files under `$TOMO_HOME/state/vault-slug-backup/<user-id>/entities/...`.
4. Backfills mention links on existing pages and rebuilds the index. It verifies
   that resolved manual page-to-page connectivity survives migration. Generated
   edges are recomputed from live facts. Raw row counts can decrease when aliases
   collapse to one canonical edge or externally edited facts no longer mention a page.

Migration and backfill are rerunnable. Retired files are archived only after the
new pages and inbound references have been written. All page writes use the
vault's account lock and atomic writer. Stop writers for an explicit repair.
A complete vault backup is still recommended before deployment: archive copies
alone are not a full rollback of rewritten inbound references.

### Managed-server deployment

Run on the actual server **after the code is available on its tracked branch**.
These commands have not been executed against the server from the local checkout.

```sh
# Before updating, stop writers and back up the authoritative vault.
tomo service stop
cp -a /root/.tomo/memory/vault "/root/.tomo/state/vault-before-links-$(date +%Y%m%d-%H%M%S)"

# Sync, install dependencies and restart. Startup runs the repair automatically.
tomo update -y

# Optional explicit per-account rebuild/backfill and JSON diagnostics.
tomo service stop
cd /root/.local/share/tomo/app
uv run python -m app.runtime.memory.vault.migrate \
  --home /root/.tomo --db /root/.tomo/state/tomo.db \
  --user scheduler \
  --user scheduler:sch_laporan_lalu_lintas_jalur_sore_17_00_wib \
  --user tg_675876469 --user usr_admin --user web
tomo service start
```

Omit `--user` to repair all vault directories. The command opens an existing DB
and never creates a new one accidentally. It reports renamed/backfilled pages,
resolved counts and remaining unresolved destinations for each account.

## Reading the graph

- `memory` list/search and `agent_info` memory sections include inbound/outbound
  related entity pages. Prompt retrieval also expands related pages.
- `/api/memory/graph` includes **all entity slugs**, including legacy hash/hub
  pages. Backlinks count distinct visible source pages, not alias spellings.
- `/api/memory/graph?include_timeline=true` also includes timeline nodes and
  provenance edges. Default entity-only graphs exclude both timeline nodes and
  their edges consistently.
- `/api/memory/overview` exposes deduplicated entity links and backlinks counts.
- `/api/memory/index` serves the current account's generated Markdown index.

All routes use the authenticated account, not a client-supplied user ID.

## Fact retrieval

Vault recall uses SQLite FTS5 only: no embedding provider, model call or new
service is required. `vault_facts`, `vault_facts_fts` and `vault_fact_pages` are
additional rebuildable indexes. Existing accounts backfill them on their next
rebuild/search without rewriting entity facts. File edits and deletion refresh
both page and fact indexes.

- Search ranks individual `§` facts with BM25. Field weights are entity 6,
  aliases 5, title 4, tags 2 and fact text 1. An exact normalized alias or entity
  key takes priority. Matching uses tokens rather than arbitrary substrings.
- Common Indonesian/English question words are excluded unless the complete
  query names an indexed alias/entity. Each remaining word expands to its
  naive singular/plural and a small built-in ID/EN synonym group
  (`_SYNONYM_GROUPS` in `read.py`, e.g. `singkat`/`concise`, `istri`/`wife`).
  With two or more words, one fact must match at least two thirds of them, so
  `favorite pet` does not return an unrelated `favorite editor` fact. A
  lexical hit still does not prove the question is answerable.
- Default recall excludes superseded facts and does not search provenance text.
  `memory(action="search", query="8000", include_superseded=true)` explicitly
  searches history and labels superseded results. Use `list` with an entity to
  inspect its complete page; search returns matching facts with provenance.
- Automatic context considers 30 candidate facts, keeps up to two facts per
  page across three direct pages, and deduplicates text. It expands links in
  selected facts to up to two additional entity pages, preferring query matches.
  Unmatched explicit neighbors contribute their first live fact. Page-level
  links from unrelated facts do not expand the prompt.
- Context preserves whole facts within 1,100 characters and an estimated 350
  tokens (the runtime's UTF-8 estimator). Oversized facts are skipped; tools
  can still read them. This is an approximate token budget, not a tokenizer.

Aliases can supply alternate names or extra bilingual vocabulary. Beyond the
fixed lexicon there is no translation, synonym inference or temporal query
interpretation.
Historical questions require the explicit history option.

Run `.venv/bin/python scripts/benchmark_retrieval.py` for an isolated,
deterministic 34-query fixture (25 positive, 9 negative) reporting Recall@5,
MRR@5, no-match accuracy and retrieval latency. It covers cross-language and
partial-match queries. The cases were written alongside the lexicon, so they
measure regressions in a small fixture, not production answer accuracy. `scripts/benchmark_memory.py`
measures server RSS and is a separate benchmark.
