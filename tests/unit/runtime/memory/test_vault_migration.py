import sqlite3

import pytest

from app.runtime.memory.vault import doc, paths, read, write
from app.runtime.memory.vault.migrate import migrate_notes
from app.runtime.memory.vault.notes import facts, scoped_key


@pytest.fixture
def db(tmp_path):
    from app.models.db import get_connection
    from app.models.schema import migrate
    conn = get_connection(tmp_path / 'migration.sqlite')
    migrate(conn)
    yield conn
    conn.close()


def note(root, relative, content):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_all_note_scopes_migrate_and_sources_disappear(tmp_path, db):
    originals = [
        note(tmp_path, 'memories/USER.md', 'Shared profile.'),
        note(tmp_path, 'memories/users/alice/USER.md', 'Private profile.'),
        note(tmp_path, 'agents/ops/MEMORY.md', 'Shared agent lesson.'),
        note(tmp_path, 'agents/ops/users/alice/MEMORY.md', 'Private agent lesson.'),
        note(tmp_path, 'workplaces/wp1/PROJECT.md', 'Project convention.'),
    ]
    assert migrate_notes(db, home_root=tmp_path) == 5
    assert not any(p.exists() for p in originals)
    assert facts('alice', 'user/profile', home_root=tmp_path) == ['Private profile.']
    assert facts('web', 'user/profile', home_root=tmp_path) == ['Shared profile.']
    assert facts('alice', scoped_key('agent', 'ops'), home_root=tmp_path) == ['Private agent lesson.']
    assert facts('web', scoped_key('project', 'wp1'), home_root=tmp_path) == ['Project convention.']
    assert migrate_notes(db, home_root=tmp_path) == 0


def test_interrupted_migration_keeps_source_and_can_retry(tmp_path, db, monkeypatch):
    source = note(tmp_path, 'memories/users/alice/USER.md', 'First fact.\n§\nSecond fact.')
    original = write._save
    def fail_after_write(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError('interrupted after writing the page')
    monkeypatch.setattr(write, '_save', fail_after_write)
    with pytest.raises(OSError):
        migrate_notes(db, home_root=tmp_path)
    assert source.is_file()
    monkeypatch.setattr(write, '_save', original)
    migrate_notes(db, home_root=tmp_path)
    assert not source.exists()
    assert facts('alice', 'user/profile', home_root=tmp_path) == ['First fact.', 'Second fact.']


def test_symlink_source_is_rejected(tmp_path, db):
    outside = tmp_path / 'source.txt'
    outside.write_text('Private fact.')
    path = tmp_path / 'memories/USER.md'
    path.parent.mkdir()
    path.symlink_to(outside)
    with pytest.raises(ValueError):
        migrate_notes(db, home_root=tmp_path)
    assert outside.read_text() == 'Private fact.'


def test_kb_migration_preserves_multiline_body_metadata_and_drops_tables(tmp_path, db):
    body = 'Reference body\n§ a literal marker\n\\§ escaped marker\n' + 'x' * 5000
    db.execute('CREATE TABLE knowledge_entries(id TEXT, title TEXT, body TEXT, tags_json TEXT, user_id TEXT, confidence REAL)')
    db.execute('INSERT INTO knowledge_entries VALUES (?,?,?,?,?,?)', ('kb1', 'Server notes', body, '["ops"]', 'alice', .9))
    db.execute('CREATE VIRTUAL TABLE knowledge_fts USING fts5(body)')
    migrate_notes(db, home_root=tmp_path)
    key = scoped_key('topic', 'knowledge:kb1')
    assert facts('alice', key, home_root=tmp_path) == [body]
    page = doc.parse(paths.entity_path('alice', key, home_root=tmp_path).read_text())
    assert page.meta['tags'] == ['ops']
    assert page.meta['confidence'] == '0.9'
    assert read.search(db, 'alice', 'Server notes', home_root=tmp_path)
    assert not read.search(db, 'bob', 'Server notes', home_root=tmp_path)
    assert not db.execute("SELECT name FROM sqlite_master WHERE name IN ('knowledge_entries','knowledge_fts')").fetchall()
    assert migrate_notes(db, home_root=tmp_path) == 0


def test_kb_verification_failure_keeps_database_source(tmp_path, db):
    db.execute('CREATE TABLE knowledge_entries(id TEXT, title TEXT, body TEXT, tags_json TEXT, user_id TEXT)')
    db.execute('INSERT INTO knowledge_entries VALUES (?,?,?,?,?)', ('kb1', 'Notes', 'Original body.', '[]', 'alice'))
    key = scoped_key('topic', 'knowledge:kb1')
    write.add_entity('alice', key, 'Conflicting page.', home_root=tmp_path, conn=db)
    with pytest.raises(RuntimeError):
        migrate_notes(db, home_root=tmp_path)
    assert db.execute('SELECT body FROM knowledge_entries').fetchone()[0] == 'Original body.'


def test_large_facts_no_storage_quota_and_markers_roundtrip(tmp_path, db):
    text = 'Project details.\n§ marker\n' + 'z' * 7000
    write.add_entity('alice', 'project/wazzapi', text, home_root=tmp_path, conn=db)
    assert facts('alice', 'project/wazzapi', home_root=tmp_path) == [text]
    assert write.correct_fact('alice', 'project/wazzapi', 0, text=text + ' Updated.', home_root=tmp_path, conn=db)
    assert facts('alice', 'project/wazzapi', home_root=tmp_path) == [text + ' Updated.']


def test_old_runtime_functions_and_tools_removed():
    from app.services import store
    from app.runtime.tools.registry import get_registry
    for name in ('create_knowledge_entry', 'search_knowledge', 'list_knowledge_entries'):
        assert not hasattr(store, name)
    registry = get_registry()
    for name in ('remember', 'recall', 'forget_memory'):
        assert registry.execute(name, {}).startswith('Error:')


def test_migration_keeps_case_variants_and_markdown(tmp_path, db):
    text = 'Preference.\n§\npreference.\n§\n~~Literal strikethrough~~'
    note(tmp_path, 'memories/users/alice/USER.md', text)
    migrate_notes(db, home_root=tmp_path)
    assert facts('alice', 'user/profile', home_root=tmp_path) == ['Preference.', 'preference.', '~~Literal strikethrough~~']


def test_sources_changed_during_upgrade_are_not_deleted(tmp_path, db, monkeypatch):
    source = note(tmp_path, 'memories/users/alice/USER.md', 'Original fact.')
    save = write._save
    def changed_source(*args, **kwargs):
        save(*args, **kwargs)
        source.write_text('Updated source fact.')
    monkeypatch.setattr(write, '_save', changed_source)
    with pytest.raises(RuntimeError, match='source changed'):
        migrate_notes(db, home_root=tmp_path)
    assert source.read_text() == 'Updated source fact.'


def test_shared_workplace_notes_reach_known_participants(tmp_path, db):
    from app.models.seed import seed_if_empty
    seed_if_empty(db)
    db.execute("INSERT INTO sessions(id,user_id,workplace_id) VALUES ('s1','alice','wp1')")
    note(tmp_path, 'workplaces/wp1/PROJECT.md', 'Workplace conventions.')
    migrate_notes(db, home_root=tmp_path)
    assert facts('alice', scoped_key('project', 'wp1'), home_root=tmp_path) == ['Workplace conventions.']
    assert not facts('bob', scoped_key('project', 'wp1'), home_root=tmp_path)


def test_concurrent_startups_serialize_migration(tmp_path, db):
    from concurrent.futures import ThreadPoolExecutor
    note(tmp_path, 'memories/users/alice/USER.md', 'Migrated exactly once.')
    db.commit()
    def upgrade():
        conn = sqlite3.connect(tmp_path / 'migration.sqlite')
        conn.row_factory = sqlite3.Row
        try:
            return migrate_notes(conn, home_root=tmp_path)
        finally:
            conn.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: upgrade(), range(2)))
    assert sorted(results) == [0, 1]
    assert facts('alice', 'user/profile', home_root=tmp_path) == ['Migrated exactly once.']


def test_tool_allowlist_migration_respects_disabled_permissions(tmp_path, db):
    from app.models.seed import seed_if_empty
    seed_if_empty(db)
    db.execute("DELETE FROM agent_tools WHERE agent_id='ops' AND tool_id='memory'")
    db.execute("INSERT INTO agent_tools(agent_id,tool_id,enabled) VALUES ('ops','recall',0)")
    migrate_notes(db, home_root=tmp_path)
    rows = db.execute("SELECT tool_id,enabled FROM agent_tools WHERE agent_id='ops' AND tool_id IN ('memory','recall','remember','forget_memory')").fetchall()
    assert [tuple(row) for row in rows] == [('memory', 0)]
