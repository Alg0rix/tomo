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
