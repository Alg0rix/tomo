"""Retrieval quality regressions over isolated authoritative Markdown."""
import sqlite3

import pytest

from app.runtime.agent.compress import _estimate_tokens
from app.runtime.memory.vault import index, paths, read, write


@pytest.fixture
def vault(tmp_path):
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    yield conn, dict(home_root=tmp_path, conn=conn)
    conn.close()


def test_old_values_require_explicit_historical_search(vault, tmp_path):
    conn, opts = vault
    write.add_entity('alice', 'tool/server', 'Port 8000.', **opts)
    write.add_entity('alice', 'tool/server', 'Port 9000.', supersedes='Port 8000.', **opts)
    assert not read.search(conn, 'alice', '8000', home_root=tmp_path)
    assert not read.snippet(conn, 'alice', '8000', home_root=tmp_path)
    hits = read.search_facts(conn, 'alice', '8000', include_superseded=True, home_root=tmp_path)
    assert [(h['text'], h['superseded']) for h in hits] == [('Port 8000.', 1)]
    hits = read.search_facts(conn, 'alice', '9000', home_root=tmp_path)
    assert [(h['text'], h['superseded']) for h in hits] == [('Port 9000.', 0)]


def test_search_returns_only_matching_facts(vault, tmp_path):
    conn, opts = vault
    write.add_entity('alice', 'user/profile', 'Favorite beverage: coffee.', **opts)
    write.add_entity('alice', 'user/profile', 'Favorite editor: Vim.', **opts)
    hits = read.search(conn, 'alice', 'beverage', home_root=tmp_path)
    assert [f['text'] for f in hits[0]['matched_facts']] == ['Favorite beverage: coffee.']
    text = read.snippet(conn, 'alice', 'beverage', home_root=tmp_path)
    assert 'coffee' in text and 'Vim' not in text


def test_entity_alias_beats_body_mentions_and_partial_words(vault, tmp_path):
    conn, opts = vault
    write.add_entity('alice', 'person/ann', 'Works on finance.', aliases=['Ann'], **opts)
    write.add_entity('alice', 'topic/mentions', 'Ann Ann Ann Ann Ann.', **opts)
    write.add_entity('alice', 'topic/planning', 'Annual planning.', **opts)
    hits = read.search(conn, 'alice', 'Ann?', home_root=tmp_path)
    assert hits[0]['slug'] == 'ann'
    assert 'planning' not in [h['slug'] for h in hits]


def test_question_words_do_not_generate_false_matches(vault, tmp_path):
    conn, opts = vault
    write.add_entity('alice', 'topic/unrelated', 'The engine is running.', **opts)
    write.add_entity('alice', 'tool/server', 'Server uses port 9000.', **opts)
    assert not read.search(conn, 'alice', 'What is my favorite beverage?', home_root=tmp_path)
    assert not read.search(conn, 'alice', 'apa yang di ini?', home_root=tmp_path)
    assert read.search(conn, 'alice', 'berapa port server?', home_root=tmp_path)[0]['slug'] == 'server'


def test_other_accounts_cannot_consume_candidate_limit(vault, tmp_path):
    conn, opts = vault
    for number in range(5):
        write.add_entity('bob', f'topic/private-{number}', 'Needle needle needle.', **opts)
    write.add_entity('alice', 'topic/public', 'Needle.', **opts)
    hits = read.search_facts(conn, 'alice', 'needle', limit=1, home_root=tmp_path)
    assert [h['slug'] for h in hits] == ['public']
    assert not read.search(conn, 'charlie', 'needle', home_root=tmp_path)


def test_existing_index_backfills_facts_without_markdown_changes(vault, tmp_path):
    conn, opts = vault
    write.add_entity('alice', 'tool/server', 'Port 9000.', **opts)
    path = paths.entity_path('alice', 'tool/server', home_root=tmp_path)
    original = path.read_bytes()
    conn.executescript('DROP TABLE vault_fact_pages; DROP TABLE vault_facts; DROP TABLE vault_facts_fts;')
    assert read.search(conn, 'alice', '9000', home_root=tmp_path)
    assert path.read_bytes() == original
    assert index.rebuild(conn, 'alice', home_root=tmp_path) == 0
    path.unlink()
    index.rebuild(conn, 'alice', home_root=tmp_path)
    assert not conn.execute('SELECT * FROM vault_facts').fetchall()
    assert not conn.execute('SELECT * FROM vault_facts_fts').fetchall()


def test_manual_fact_edit_refreshes_search(vault, tmp_path):
    conn, opts = vault
    write.add_entity('alice', 'tool/server', 'Port 9000.', **opts)
    path = paths.entity_path('alice', 'tool/server', home_root=tmp_path)
    path.write_text(path.read_text().replace('9000', '7000'))
    assert not read.search(conn, 'alice', '9000', home_root=tmp_path)
    assert read.search(conn, 'alice', '7000', home_root=tmp_path)


def test_context_budgets_keep_whole_facts_and_skip_oversized_candidates(vault, tmp_path):
    conn, opts = vault
    write.add_entity('alice', 'topic/length', 'Needle ' * 400, **opts)
    write.add_entity('alice', 'topic/length', 'Needle is here.', **opts)
    output = read.snippet(conn, 'alice', 'needle', budget=100, token_budget=25, home_root=tmp_path)
    assert output.endswith('Needle is here.')
    assert len(output) <= 100 and _estimate_tokens(output) <= 25
    assert not read.snippet(conn, 'alice', 'needle', budget=1, home_root=tmp_path)
    assert not read.snippet(conn, 'alice', 'needle', token_budget=1, home_root=tmp_path)


def test_graph_expansion_follows_selected_fact_not_unrelated_page_edges(vault, tmp_path):
    conn, opts = vault
    write.add_entity('alice', 'tool/python', 'Implementation language.', **opts)
    write.add_entity('alice', 'tool/other', 'Unrelated tool.', **opts)
    write.add_entity('alice', 'project/tomo', 'Runtime uses [[tool/python]].', **opts)
    write.add_entity('alice', 'project/tomo', 'Deployment uses [[tool/other]].', **opts)
    output = read.snippet(conn, 'alice', 'runtime', home_root=tmp_path)
    assert 'Implementation language.' in output
    assert 'Unrelated tool.' not in output


def test_graph_expansion_never_imports_cross_account_facts(vault, tmp_path):
    conn, opts = vault
    write.add_entity('bob', 'tool/private', 'Secret neighbor.', **opts)
    write.add_entity('alice', 'project/tomo', 'Runtime uses [[bob/entities/tool/private.md]].', **opts)
    output = read.snippet(conn, 'alice', 'runtime', home_root=tmp_path)
    assert 'Secret neighbor.' not in output


def test_context_deduplicates_facts(vault, tmp_path):
    conn, opts = vault
    write.add_entity('alice', 'topic/first', 'Needle is here.', **opts)
    write.add_entity('alice', 'topic/second', 'Needle is here.', **opts)
    assert read.snippet(conn, 'alice', 'needle', home_root=tmp_path).count('Needle is here.') == 1


def test_memory_tool_uses_live_matches_and_labels_historical_facts(vault, tmp_path, monkeypatch):
    from app.core import config
    from app.runtime.tools import memory, user_ctx
    from app.services import store

    conn, opts = vault
    monkeypatch.setattr(config, 'TOMO_HOME', tmp_path)
    monkeypatch.setattr(user_ctx, 'current_user_id', lambda: 'alice')
    monkeypatch.setattr(store, 'with_db', lambda fn: fn(conn))
    write.add_entity('alice', 'tool/server', 'Port 8000.', **opts)
    write.add_entity('alice', 'tool/server', 'Port 9000.', supersedes='Port 8000.', **opts)
    assert memory.run({'action': 'search', 'query': '8000'}) == 'Vault has no matching facts.'
    assert '[superseded] Port 8000.' in memory.run(
        {'action': 'search', 'query': '8000', 'include_superseded': True})
    assert '8000' not in memory.run({'action': 'search', 'query': 'server'})


def test_fts_query_punctuation_and_operators_are_safe(vault, tmp_path):
    conn, opts = vault
    write.add_entity('alice', 'tool/server', 'Port 9000.', **opts)
    assert read.search(conn, 'alice', '"9000" OR (', home_root=tmp_path)
    assert not read.search(conn, 'alice', '" * : ()', home_root=tmp_path)
    assert not read.search(conn, 'alice', '9000', limit=0, home_root=tmp_path)


def test_multi_term_query_needs_most_terms_on_one_fact(vault, tmp_path):
    conn, opts = vault
    write.add_entity('alice', 'user/profile', 'Favorite editor: Vim.', **opts)
    write.add_entity('alice', 'person/rina', "Rina is the user's wife.", **opts)
    write.add_entity('alice', 'person/budi', 'Budi is a friend.', **opts)
    assert not read.search(conn, 'alice', 'What is my favorite pet?', home_root=tmp_path)
    assert not read.search(conn, 'alice', 'istri budi', home_root=tmp_path)
    assert read.search(conn, 'alice', 'favorite editor', home_root=tmp_path)[0]['slug'] == 'profile'


def test_lexicon_bridges_indonesian_queries_to_english_facts(vault, tmp_path):
    conn, opts = vault
    write.add_entity('alice', 'user/profile', 'The user prefers concise answers.', **opts)
    write.add_entity('alice', 'person/rina', "Rina is the user's wife.", **opts)
    assert read.search(conn, 'alice', 'jawaban singkat', home_root=tmp_path)[0]['slug'] == 'profile'
    assert read.search(conn, 'alice', 'siapa istri saya', home_root=tmp_path)[0]['slug'] == 'rina'
