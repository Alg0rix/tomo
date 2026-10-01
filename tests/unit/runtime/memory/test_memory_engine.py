from app.runtime.memory.fts import _fts_query
from app.runtime.memory.vault import read, write


def test_safe_fts_query():
    assert _fts_query('server OR "NEAR"') == '"server" OR "or" OR "near"'


def test_vault_search_is_scoped_and_index_rebuildable(tmp_path):
    import sqlite3
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    write.add_entity('alice', 'tool/server', 'Server uses port 9000.', home_root=tmp_path, conn=conn)
    assert read.search(conn, 'alice', 'port', home_root=tmp_path)
    assert not read.search(conn, 'bob', 'port', home_root=tmp_path)
    conn.execute('DELETE FROM vault_docs')
    assert read.search(conn, 'alice', 'port', home_root=tmp_path)
    conn.close()
